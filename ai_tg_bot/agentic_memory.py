"""
Agentic Memory System - точная копия из https://github.com/ALucek/agentic-memory
Адаптировано для Telegram бота.
"""

import logging
import os
from collections import defaultdict, deque

import weaviate
from weaviate.classes.config import Property, DataType, Configure
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser


# =============================================================================
# REFLECTION PROMPT (из оригинала)
# =============================================================================

REFLECTION_PROMPT_TEMPLATE = """
You are analyzing conversations to create memories that will help guide future interactions. Your task is to extract key elements that would be most helpful when encountering similar discussions in the future.

Review the conversation and create a memory reflection following these rules:

1. For any field where you don't have enough information or the field isn't relevant, use "N/A"
2. Be extremely concise - each string should be one clear, actionable sentence
3. Focus only on information that would be useful for handling similar future conversations
4. Context_tags should be specific enough to match similar situations but general enough to be reusable

Output valid JSON in exactly this format:
{{
    "context_tags": [
        string,
        ...
    ],
    "conversation_summary": string,
    "what_worked": string,
    "what_to_avoid": string
}}

Examples:
- Good context_tags: ["project_management", "reporting", "onboarding"]
- Bad context_tags: ["conversation", "chat", "questions"]

- Good conversation_summary: "Explained how to create a project report following company guidelines"
- Bad conversation_summary: "Discussed work topics"

- Good what_worked: "Providing step-by-step instructions with concrete examples"
- Bad what_worked: "Explained well"

- Good what_to_avoid: "Giving too many options without clear recommendation"
- Bad what_to_avoid: "Was confusing"

Do not include any text outside the JSON object in your response.

Here is the prior conversation:

{conversation}
"""

PROCEDURAL_UPDATE_PROMPT = """You are maintaining a continuously updated list of the most important procedural behavior instructions for an AI assistant. Your task is to refine and improve a list of key takeaways based on new conversation feedback while maintaining the most valuable existing insights.

CURRENT TAKEAWAYS:
{current_takeaways}

NEW FEEDBACK:
What Worked Well:
{what_worked}

What To Avoid:
{what_to_avoid}

Please generate an updated list of up to 10 key takeaways that combines:
1. The most valuable insights from the current takeaways
2. New learnings from the recent feedback
3. Any synthesized insights combining multiple learnings

Requirements for each takeaway:
- Must be specific and actionable
- Should address a distinct aspect of behavior
- Include a clear rationale
- Written in imperative form (e.g., "Maintain conversation context by...")

Format each takeaway as:
[#]. [Instruction] - [Brief rationale]

The final list should:
- Be ordered by importance/impact
- Cover a diverse range of interaction aspects
- Focus on concrete behaviors rather than abstract principles
- Preserve particularly valuable existing takeaways
- Incorporate new insights when they provide meaningful improvements

Return up to but no more than 10 takeaways, replacing or combining existing ones as needed to maintain the most effective set of guidelines.
Return only the list, no preamble or explanation.
"""


class AgenticMemory:
    """
    Полная реализация 4-уровневой памяти из agentic-memory репозитория.
    Использует Weaviate для векторного хранения и гибридного поиска.
    """

    def __init__(
        self,
        llm_base_url: str,
        llm_model: str,
        persona_path: str = "persona.txt",
        procedural_path: str = "procedural_memory.txt",
        weaviate_host: str = "localhost",
        weaviate_port: int = 8080,
        short_term_limit: int = 12,
    ):
        self.short_term_limit = short_term_limit
        self.procedural_path = procedural_path

        # LangChain LLM (используем OpenAI-совместимый API)
        self.llm = ChatOpenAI(
            base_url=llm_base_url,
            api_key="lm-studio",
            model=llm_model,
            temperature=0.7,
        )

        # Reflection chain (из оригинала)
        self.reflection_prompt = ChatPromptTemplate.from_template(REFLECTION_PROMPT_TEMPLATE)
        self.reflect_chain = self.reflection_prompt | self.llm | JsonOutputParser()

        # Weaviate client
        self.vdb_client = weaviate.connect_to_local(
            host=weaviate_host,
            port=weaviate_port,
        )
        logging.info(f"[WEAVIATE] Connected: {self.vdb_client.is_ready()}")

        # Создаём коллекции если не существуют
        self._init_collections()

        # Working Memory — текущий контекст
        self.short_term = defaultdict(lambda: deque(maxlen=short_term_limit))

        # Episodic state
        self.conversations = []
        self.what_worked = set()
        self.what_to_avoid = set()

        # Persona
        self.persona = self._load_file(persona_path)

        logging.info("[AGENTIC] Memory system initialized")

    def _load_file(self, path: str) -> str:
        """Загружает содержимое файла."""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    def _save_file(self, path: str, content: str):
        """Сохраняет содержимое в файл."""
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _init_collections(self):
        """Создаёт коллекции Weaviate если не существуют."""
        collections = self.vdb_client.collections.list_all()
        collection_names = [c.lower() for c in collections]

        # Episodic Memory Collection (используем дефолтный vectorizer)
        if "episodic_memory" not in collection_names:
            self.vdb_client.collections.create(
                name="episodic_memory",
                description="Historical chat interactions and takeaways",
                properties=[
                    Property(name="conversation", data_type=DataType.TEXT),
                    Property(name="context_tags", data_type=DataType.TEXT_ARRAY),
                    Property(name="conversation_summary", data_type=DataType.TEXT),
                    Property(name="what_worked", data_type=DataType.TEXT),
                    Property(name="what_to_avoid", data_type=DataType.TEXT),
                ]
            )
            logging.info("[WEAVIATE] Created episodic_memory collection")

        # Semantic Memory Collection (Knowledge Base)
        if "knowledge_base" not in collection_names:
            self.vdb_client.collections.create(
                name="knowledge_base",
                description="Knowledge chunks from documents (Notion, etc.)",
                properties=[
                    Property(name="chunk", data_type=DataType.TEXT),
                    Property(name="title", data_type=DataType.TEXT),
                    Property(name="source", data_type=DataType.TEXT),
                ]
            )
            logging.info("[WEAVIATE] Created knowledge_base collection")

    # =========================================================================
    # WORKING MEMORY
    # =========================================================================

    def add_message(self, chat_id: int, role: str, content: str):
        """Добавляет сообщение в working memory."""
        self.short_term[chat_id].append({
            "role": role,
            "content": content
        })

    def get_working_memory(self, chat_id: int) -> list:
        """Возвращает текущий контекст разговора."""
        return list(self.short_term[chat_id])

    def format_conversation(self, messages: list) -> str:
        """Форматирует сообщения в строку (из оригинала)."""
        conversation = []
        for message in messages:
            role = message.get("role", "unknown").upper()
            content = message.get("content", "")
            conversation.append(f"{role}: {content}")
        return "\n".join(conversation)

    # =========================================================================
    # EPISODIC MEMORY (из оригинала)
    # =========================================================================

    def add_episodic_memory(self, chat_id: int):
        """Сохраняет разговор в episodic memory с рефлексией."""
        messages = self.get_working_memory(chat_id)
        if len(messages) < 4:
            return

        conversation = self.format_conversation(messages)

        try:
            reflection = self.reflect_chain.invoke({"conversation": conversation})
            logging.info(f"[EPISODIC] Reflection: {reflection}")

            episodic_memory = self.vdb_client.collections.get("episodic_memory")
            episodic_memory.data.insert({
                "conversation": conversation,
                "context_tags": reflection.get("context_tags", []),
                "conversation_summary": reflection.get("conversation_summary", "N/A"),
                "what_worked": reflection.get("what_worked", "N/A"),
                "what_to_avoid": reflection.get("what_to_avoid", "N/A"),
            })
            logging.info("[EPISODIC] Memory stored")
        except Exception as e:
            logging.warning(f"[EPISODIC] Error: {e}")

    def episodic_recall(self, query: str, limit: int = 1):
        """Гибридный поиск в episodic memory (из оригинала)."""
        try:
            episodic_memory = self.vdb_client.collections.get("episodic_memory")
            memory = episodic_memory.query.hybrid(
                query=query,
                alpha=0.5,  # 50% semantic, 50% keyword
                limit=limit,
            )
            return memory
        except Exception as e:
            logging.warning(f"[EPISODIC] Recall error: {e}")
            return None

    def build_episodic_context(self, query: str) -> dict:
        """Строит контекст из episodic memory (из оригинала)."""
        memory = self.episodic_recall(query)

        if not memory or not memory.objects:
            return {
                "current_conversation": "",
                "previous_conversations": [],
                "what_worked": list(self.what_worked),
                "what_to_avoid": list(self.what_to_avoid),
            }

        current_conversation = memory.objects[0].properties.get("conversation", "")

        if current_conversation and current_conversation not in self.conversations:
            self.conversations.append(current_conversation)

        worked = memory.objects[0].properties.get("what_worked", "")
        if worked and worked != "N/A":
            self.what_worked.update(worked.split(". "))

        avoid = memory.objects[0].properties.get("what_to_avoid", "")
        if avoid and avoid != "N/A":
            self.what_to_avoid.update(avoid.split(". "))

        previous_convos = [
            conv for conv in self.conversations[-4:]
            if conv != current_conversation
        ][-3:]

        return {
            "current_conversation": current_conversation,
            "previous_conversations": previous_convos,
            "what_worked": list(self.what_worked),
            "what_to_avoid": list(self.what_to_avoid),
        }

    # =========================================================================
    # SEMANTIC MEMORY (Knowledge Base) — из оригинала
    # =========================================================================

    def add_knowledge(self, chunks: list, title: str = "", source: str = "unknown"):
        """Добавляет чанки в knowledge base."""
        knowledge_base = self.vdb_client.collections.get("knowledge_base")

        for chunk in chunks:
            if not chunk.strip():
                continue
            knowledge_base.data.insert({
                "chunk": chunk,
                "title": title,
                "source": source,
            })

        logging.info(f"[SEMANTIC] Added {len(chunks)} chunks from {source}")

    def semantic_recall(self, query: str, limit: int = 15) -> str:
        """Гибридный поиск в knowledge base (из оригинала)."""
        try:
            knowledge_base = self.vdb_client.collections.get("knowledge_base")
            memories = knowledge_base.query.hybrid(
                query=query,
                alpha=0.5,  # 50% semantic, 50% keyword
                limit=limit,
            )

            combined_text = ""
            for i, memory in enumerate(memories.objects):
                chunk = memory.properties.get("chunk", "").strip()
                if chunk:
                    if i > 0:
                        combined_text += f"\n\nCHUNK {i + 1}:\n"
                    combined_text += chunk

            logging.info(f"[SEMANTIC] Recalled {len(memories.objects)} chunks")
            return combined_text

        except Exception as e:
            logging.warning(f"[SEMANTIC] Recall error: {e}")
            return ""

    def semantic_rag(self, query: str) -> str:
        """Создаёт RAG контекст (из оригинала)."""
        memories = self.semantic_recall(query)

        if not memories:
            return ""

        semantic_prompt = f"""If needed, use this grounded context to factually answer the next question.
Let me know if you do not have enough information or context to answer a question.

{memories}
"""
        return semantic_prompt

    # =========================================================================
    # PROCEDURAL MEMORY (из оригинала)
    # =========================================================================

    def get_procedural_memory(self) -> str:
        """Возвращает текущие правила поведения."""
        return self._load_file(self.procedural_path)

    def update_procedural_memory(self):
        """Обновляет правила на основе накопленного опыта (из оригинала)."""
        current_takeaways = self.get_procedural_memory()

        if not self.what_worked and not self.what_to_avoid:
            return

        prompt = PROCEDURAL_UPDATE_PROMPT.format(
            current_takeaways=current_takeaways or "No existing takeaways.",
            what_worked="\n".join(self.what_worked) or "N/A",
            what_to_avoid="\n".join(self.what_to_avoid) or "N/A",
        )

        try:
            response = self.llm.invoke(prompt)
            new_rules = response.content.strip()
            self._save_file(self.procedural_path, new_rules)
            logging.info("[PROCEDURAL] Memory updated")
        except Exception as e:
            logging.warning(f"[PROCEDURAL] Update error: {e}")

    # =========================================================================
    # SYSTEM PROMPT BUILDER (из оригинала)
    # =========================================================================

    def build_system_prompt(self, query: str) -> str:
        """Строит system prompt со всеми типами памяти (из оригинала)."""
        # Procedural memory
        procedural = self.get_procedural_memory()

        # Episodic context
        episodic = self.build_episodic_context(query)

        # Build prompt
        prompt_parts = [self.persona]

        if episodic["current_conversation"]:
            prompt_parts.append(f"""
You recall similar conversations with the user, here are the details:

Current Conversation Match: {episodic['current_conversation']}
Previous Conversations: {' | '.join(episodic['previous_conversations'])}
What has worked well: {' '.join(episodic['what_worked'])}
What to avoid: {' '.join(episodic['what_to_avoid'])}

Use these memories as context for your response to the user.""")

        if procedural:
            prompt_parts.append(f"""
Additionally, here are guidelines for interactions:
{procedural}""")

        return "\n".join(prompt_parts)

    # =========================================================================
    # MAIN INTERFACE
    # =========================================================================

    def get_context_for_llm(self, chat_id: int, user_query: str) -> list:
        """
        Возвращает полный контекст для LLM.
        Включает: system prompt, working memory, semantic RAG.
        """
        # System prompt (persona + episodic + procedural)
        system_prompt = self.build_system_prompt(user_query)

        # Working memory (последние сообщения)
        working_memory = self.get_working_memory(chat_id)[-6:]

        # Semantic RAG (knowledge base)
        rag_context = self.semantic_rag(user_query)

        # Build messages
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(working_memory)

        # Add RAG as context if available
        if rag_context:
            messages.append({
                "role": "user",
                "content": rag_context
            })

        # Add current query
        messages.append({"role": "user", "content": user_query})

        return messages

    def chat(self, messages: list) -> str:
        """Вызывает LLM с сообщениями."""
        # Convert to LangChain format
        lc_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            elif role == "assistant":
                lc_messages.append(SystemMessage(content=f"Assistant: {content}"))
            else:
                lc_messages.append(HumanMessage(content=content))

        response = self.llm.invoke(lc_messages)
        return response.content

    def finalize_session(self, chat_id: int):
        """Финализирует сессию — сохраняет episodic и обновляет procedural."""
        self.add_episodic_memory(chat_id)
        self.update_procedural_memory()

    def close(self):
        """Закрывает соединение с Weaviate."""
        if self.vdb_client:
            self.vdb_client.close()
            logging.info("[WEAVIATE] Connection closed")
