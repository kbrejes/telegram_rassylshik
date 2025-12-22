"""
Conversation Memory System

Implements a 4-level memory architecture:
1. Working Memory - current conversation context (in-memory)
2. Episodic Memory - past conversations with reflections (Weaviate)
3. Semantic Memory - knowledge base (Weaviate)
4. Procedural Memory - behavioral rules (file-based)

Based on https://github.com/ALucek/agentic-memory
"""

import os
import json
import logging
from collections import defaultdict, deque
from typing import Optional, List, Dict, Any

from .llm_client import UnifiedLLMClient

logger = logging.getLogger(__name__)


# =============================================================================
# PROMPTS
# =============================================================================

REFLECTION_PROMPT = """You are analyzing conversations to create memories that will help guide future interactions.

Review the conversation and create a memory reflection as JSON:

{{
    "context_tags": ["tag1", "tag2", "tag3"],
    "conversation_summary": "One sentence summary",
    "what_worked": "What approach worked well",
    "what_to_avoid": "What to avoid in future"
}}

Examples:
- Good context_tags: ["lead_qualification", "pricing_discussion", "technical_questions"]
- Bad context_tags: ["conversation", "chat"]

For any field without enough info, use "N/A".
Return only valid JSON.

Conversation:
{conversation}
"""

PROCEDURAL_UPDATE_PROMPT = """You maintain a list of behavioral guidelines for an AI sales assistant.

CURRENT GUIDELINES:
{current_takeaways}

NEW FEEDBACK:
What Worked Well:
{what_worked}

What To Avoid:
{what_to_avoid}

Generate an updated list of up to 10 guidelines that:
1. Keeps valuable existing insights
2. Incorporates new learnings
3. Uses imperative form

Format:
[#]. [Instruction] - [Brief rationale]

Return only the list, no explanation.
"""


class ConversationMemory:
    """
    4-level memory system for AI conversations.

    Supports optional Weaviate for episodic/semantic memory.
    Falls back to in-memory storage if Weaviate unavailable.
    """

    def __init__(
        self,
        llm_client: UnifiedLLMClient,
        persona_path: str = "personas/default.txt",
        procedural_path: str = "procedural_memory.txt",
        weaviate_host: str = "localhost",
        weaviate_port: int = 8080,
        short_term_limit: int = 12,
        use_weaviate: bool = True,
    ):
        """
        Initialize memory system.

        Args:
            llm_client: LLM client for reflections
            persona_path: Path to persona file
            procedural_path: Path to procedural memory file
            weaviate_host: Weaviate host
            weaviate_port: Weaviate port
            short_term_limit: Max messages in working memory
            use_weaviate: Whether to use Weaviate (optional)
        """
        self.llm = llm_client
        self.short_term_limit = short_term_limit
        self.procedural_path = procedural_path
        self.persona_path = persona_path

        # Working Memory - current context per contact
        self.working_memory: Dict[int, deque] = defaultdict(
            lambda: deque(maxlen=short_term_limit)
        )

        # Episodic state (in-memory fallback)
        self.conversations: List[str] = []
        self.what_worked: set = set()
        self.what_to_avoid: set = set()

        # Knowledge base (in-memory fallback)
        self.knowledge_chunks: List[Dict[str, str]] = []

        # Weaviate
        self.vdb_client = None
        self.use_weaviate = use_weaviate

        if use_weaviate:
            self._init_weaviate(weaviate_host, weaviate_port)

        # Load persona
        self.persona = self._load_file(persona_path)

        logger.info(f"[MEMORY] Initialized (weaviate={self.vdb_client is not None})")

    def _init_weaviate(self, host: str, port: int):
        """Initialize Weaviate connection."""
        try:
            import weaviate
            from weaviate.classes.config import Property, DataType

            self.vdb_client = weaviate.connect_to_local(host=host, port=port)

            if not self.vdb_client.is_ready():
                logger.warning("[WEAVIATE] Not ready, falling back to in-memory")
                self.vdb_client = None
                return

            # Create collections
            collections = self.vdb_client.collections.list_all()
            collection_names = [c.lower() for c in collections]

            if "episodic_memory" not in collection_names:
                self.vdb_client.collections.create(
                    name="episodic_memory",
                    properties=[
                        Property(name="conversation", data_type=DataType.TEXT),
                        Property(name="context_tags", data_type=DataType.TEXT_ARRAY),
                        Property(name="conversation_summary", data_type=DataType.TEXT),
                        Property(name="what_worked", data_type=DataType.TEXT),
                        Property(name="what_to_avoid", data_type=DataType.TEXT),
                        Property(name="channel_id", data_type=DataType.TEXT),
                    ]
                )
                logger.info("[WEAVIATE] Created episodic_memory collection")

            if "knowledge_base" not in collection_names:
                self.vdb_client.collections.create(
                    name="knowledge_base",
                    properties=[
                        Property(name="chunk", data_type=DataType.TEXT),
                        Property(name="title", data_type=DataType.TEXT),
                        Property(name="source", data_type=DataType.TEXT),
                    ]
                )
                logger.info("[WEAVIATE] Created knowledge_base collection")

            logger.info("[WEAVIATE] Connected successfully")

        except ImportError:
            logger.warning("[WEAVIATE] weaviate-client not installed, using in-memory")
            self.vdb_client = None
        except Exception as e:
            logger.warning(f"[WEAVIATE] Connection failed: {e}, using in-memory")
            self.vdb_client = None

    def _load_file(self, path: str) -> str:
        """Load file content."""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return ""

    def _save_file(self, path: str, content: str):
        """Save content to file."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # =========================================================================
    # WORKING MEMORY
    # =========================================================================

    def add_message(self, contact_id: int, role: str, content: str):
        """Add message to working memory."""
        self.working_memory[contact_id].append({
            "role": role,
            "content": content
        })
        logger.debug(f"[WORKING] Added {role} message for contact {contact_id}")

    def get_working_memory(self, contact_id: int) -> List[Dict[str, str]]:
        """Get current conversation context."""
        return list(self.working_memory[contact_id])

    def clear_working_memory(self, contact_id: int):
        """Clear working memory for contact."""
        self.working_memory[contact_id].clear()

    def format_conversation(self, messages: List[Dict[str, str]]) -> str:
        """Format messages as string."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    # =========================================================================
    # EPISODIC MEMORY
    # =========================================================================

    async def add_episodic_memory(self, contact_id: int, channel_id: str = ""):
        """Save conversation with reflection to episodic memory."""
        messages = self.get_working_memory(contact_id)
        if len(messages) < 4:
            return

        conversation = self.format_conversation(messages)

        try:
            # Generate reflection
            prompt = REFLECTION_PROMPT.format(conversation=conversation)
            response = await self.llm.achat([
                {"role": "system", "content": "You analyze conversations and output JSON."},
                {"role": "user", "content": prompt}
            ])

            # Parse JSON
            reflection = json.loads(response)
            logger.info(f"[EPISODIC] Reflection: {reflection}")

            # Store
            if self.vdb_client:
                episodic = self.vdb_client.collections.get("episodic_memory")
                episodic.data.insert({
                    "conversation": conversation,
                    "context_tags": reflection.get("context_tags", []),
                    "conversation_summary": reflection.get("conversation_summary", "N/A"),
                    "what_worked": reflection.get("what_worked", "N/A"),
                    "what_to_avoid": reflection.get("what_to_avoid", "N/A"),
                    "channel_id": channel_id,
                })
            else:
                # In-memory fallback
                self.conversations.append(conversation)

            # Update local state
            worked = reflection.get("what_worked", "")
            if worked and worked != "N/A":
                self.what_worked.add(worked)

            avoid = reflection.get("what_to_avoid", "")
            if avoid and avoid != "N/A":
                self.what_to_avoid.add(avoid)

            logger.info("[EPISODIC] Memory stored")

        except json.JSONDecodeError as e:
            logger.warning(f"[EPISODIC] JSON parse error: {e}")
        except Exception as e:
            logger.warning(f"[EPISODIC] Error: {e}")

    def episodic_recall(self, query: str, limit: int = 1) -> Optional[Any]:
        """Search episodic memory."""
        if not self.vdb_client:
            return None

        try:
            episodic = self.vdb_client.collections.get("episodic_memory")
            return episodic.query.hybrid(
                query=query,
                alpha=0.5,
                limit=limit,
            )
        except Exception as e:
            logger.warning(f"[EPISODIC] Recall error: {e}")
            return None

    def build_episodic_context(self, query: str) -> Dict[str, Any]:
        """Build context from episodic memory."""
        memory = self.episodic_recall(query)

        if not memory or not memory.objects:
            return {
                "current_conversation": "",
                "previous_conversations": [],
                "what_worked": list(self.what_worked),
                "what_to_avoid": list(self.what_to_avoid),
            }

        current = memory.objects[0].properties.get("conversation", "")

        if current and current not in self.conversations:
            self.conversations.append(current)

        worked = memory.objects[0].properties.get("what_worked", "")
        if worked and worked != "N/A":
            self.what_worked.add(worked)

        avoid = memory.objects[0].properties.get("what_to_avoid", "")
        if avoid and avoid != "N/A":
            self.what_to_avoid.add(avoid)

        previous = [c for c in self.conversations[-4:] if c != current][-3:]

        return {
            "current_conversation": current,
            "previous_conversations": previous,
            "what_worked": list(self.what_worked),
            "what_to_avoid": list(self.what_to_avoid),
        }

    # =========================================================================
    # SEMANTIC MEMORY (Knowledge Base)
    # =========================================================================

    def add_knowledge(self, chunks: List[str], title: str = "", source: str = "local"):
        """Add knowledge chunks."""
        if self.vdb_client:
            kb = self.vdb_client.collections.get("knowledge_base")
            for chunk in chunks:
                if chunk.strip():
                    kb.data.insert({
                        "chunk": chunk,
                        "title": title,
                        "source": source,
                    })
        else:
            for chunk in chunks:
                if chunk.strip():
                    self.knowledge_chunks.append({
                        "chunk": chunk,
                        "title": title,
                        "source": source,
                    })

        logger.info(f"[SEMANTIC] Added {len(chunks)} chunks from {source}")

    def load_knowledge_file(self, file_path: str, chunk_size: int = 500):
        """Load knowledge from file."""
        content = self._load_file(file_path)
        if not content:
            return

        # Simple chunking by paragraphs
        paragraphs = content.split("\n\n")
        chunks = []
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) < chunk_size:
                current_chunk += para + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para + "\n\n"

        if current_chunk:
            chunks.append(current_chunk.strip())

        title = os.path.basename(file_path)
        self.add_knowledge(chunks, title=title, source=f"file:{file_path}")

    def semantic_recall(self, query: str, limit: int = 5) -> str:
        """Search knowledge base."""
        if self.vdb_client:
            try:
                kb = self.vdb_client.collections.get("knowledge_base")
                results = kb.query.hybrid(
                    query=query,
                    alpha=0.5,
                    limit=limit,
                )

                chunks = []
                for obj in results.objects:
                    chunk = obj.properties.get("chunk", "").strip()
                    if chunk:
                        chunks.append(chunk)

                return "\n\n".join(chunks)

            except Exception as e:
                logger.warning(f"[SEMANTIC] Recall error: {e}")
                return ""
        else:
            # Simple keyword search fallback
            query_lower = query.lower()
            relevant = [
                c["chunk"] for c in self.knowledge_chunks
                if query_lower in c["chunk"].lower()
            ]
            return "\n\n".join(relevant[:limit])

    # =========================================================================
    # PROCEDURAL MEMORY
    # =========================================================================

    def get_procedural_memory(self) -> str:
        """Get current behavioral guidelines."""
        return self._load_file(self.procedural_path)

    async def update_procedural_memory(self):
        """Update procedural rules based on experience."""
        current = self.get_procedural_memory()

        if not self.what_worked and not self.what_to_avoid:
            return

        prompt = PROCEDURAL_UPDATE_PROMPT.format(
            current_takeaways=current or "No existing guidelines.",
            what_worked="\n".join(self.what_worked) or "N/A",
            what_to_avoid="\n".join(self.what_to_avoid) or "N/A",
        )

        try:
            response = await self.llm.achat([
                {"role": "system", "content": "You are a guidelines optimizer."},
                {"role": "user", "content": prompt}
            ])

            self._save_file(self.procedural_path, response.strip())
            logger.info("[PROCEDURAL] Memory updated")

        except Exception as e:
            logger.warning(f"[PROCEDURAL] Update error: {e}")

    # =========================================================================
    # SYSTEM PROMPT BUILDER
    # =========================================================================

    def build_system_prompt(self, query: str) -> str:
        """Build system prompt with all memory types."""
        parts = [self.persona] if self.persona else []

        # Episodic context
        episodic = self.build_episodic_context(query)

        if episodic["current_conversation"]:
            parts.append(f"""
You recall similar conversations:

Current Match: {episodic['current_conversation'][:500]}...
Previous: {' | '.join(c[:200] for c in episodic['previous_conversations'])}
What worked: {' '.join(episodic['what_worked'])}
What to avoid: {' '.join(episodic['what_to_avoid'])}

Use these as context.""")

        # Procedural rules
        procedural = self.get_procedural_memory()
        if procedural:
            parts.append(f"""
Guidelines:
{procedural}""")

        return "\n\n".join(parts)

    # =========================================================================
    # MAIN INTERFACE
    # =========================================================================

    async def get_context_for_llm(
        self,
        contact_id: int,
        user_query: str,
        include_knowledge: bool = True,
    ) -> List[Dict[str, str]]:
        """
        Build full context for LLM.

        Returns list of messages ready for LLM.
        """
        messages = []

        # System prompt (persona + episodic + procedural)
        system_prompt = self.build_system_prompt(user_query)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Working memory (recent conversation)
        working = self.get_working_memory(contact_id)[-6:]
        messages.extend(working)

        # Knowledge base (RAG)
        if include_knowledge:
            knowledge = self.semantic_recall(user_query)
            if knowledge:
                messages.append({
                    "role": "user",
                    "content": f"Relevant knowledge:\n\n{knowledge}\n\nUse this to answer if helpful."
                })

        # Current query
        messages.append({"role": "user", "content": user_query})

        return messages

    async def generate_response(
        self,
        contact_id: int,
        user_message: str,
        include_knowledge: bool = True,
    ) -> str:
        """
        Generate AI response for user message.

        Handles full flow: context -> LLM -> memory update.
        """
        # Get context
        messages = await self.get_context_for_llm(
            contact_id, user_message, include_knowledge
        )

        # Generate response
        response = await self.llm.achat(messages)

        # Update working memory
        self.add_message(contact_id, "user", user_message)
        self.add_message(contact_id, "assistant", response)

        return response

    async def finalize_session(self, contact_id: int, channel_id: str = ""):
        """Finalize session - save to episodic and update procedural."""
        await self.add_episodic_memory(contact_id, channel_id)
        await self.update_procedural_memory()

    def close(self):
        """Close Weaviate connection."""
        if self.vdb_client:
            self.vdb_client.close()
            logger.info("[WEAVIATE] Connection closed")
