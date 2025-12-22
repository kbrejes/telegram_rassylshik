from collections import defaultdict, deque
import faiss
import numpy as np
import os
import pickle
import json
import logging
from datetime import datetime


class EpisodicMemory:
    """Хранит рефлексии прошлых разговоров: что работало, что избегать."""

    def __init__(self, llm, embedding_dim=768):
        self.llm = llm
        self.embedding_dim = embedding_dim
        self.index = faiss.IndexFlatIP(embedding_dim)  # Inner Product для косинусного сходства
        self.episodes = []  # Список эпизодов с метаданными

    def reflect_on_conversation(self, messages: list) -> dict:
        """Анализирует разговор и создаёт рефлексию."""
        if len(messages) < 4:
            return None

        conversation_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in messages
        )

        prompt = [
            {"role": "system", "content": """Ты анализируешь разговоры для создания памяти.
Выведи JSON с полями:
- context_tags: список из 2-4 ключевых тем разговора
- summary: краткое описание разговора (1-2 предложения)
- what_worked: что хорошо сработало в этом разговоре
- what_to_avoid: чего следует избегать в будущем
- user_preferences: замеченные предпочтения пользователя

Если информации недостаточно, используй "N/A".
Отвечай ТОЛЬКО валидным JSON, без markdown."""},
            {"role": "user", "content": f"Проанализируй разговор:\n\n{conversation_text}"}
        ]

        try:
            response = self.llm.chat(prompt, temperature=0.3)
            # Убираем возможные markdown-обёртки
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            reflection = json.loads(response)
            return reflection
        except Exception as e:
            logging.warning(f"[EPISODIC] Ошибка рефлексии: {e}")
            return None

    def add_episode(self, chat_id: int, messages: list, reflection: dict):
        """Добавляет эпизод в память."""
        if not reflection:
            return

        # Создаём текст для эмбеддинга
        embed_text = f"{reflection.get('summary', '')} {' '.join(reflection.get('context_tags', []))}"

        try:
            embedding = self.llm.embed(embed_text)
            # Нормализуем для косинусного сходства
            embedding = embedding / np.linalg.norm(embedding)
            self.index.add(embedding.reshape(1, -1))

            episode = {
                "chat_id": chat_id,
                "timestamp": datetime.now().isoformat(),
                "reflection": reflection,
                "message_count": len(messages)
            }
            self.episodes.append(episode)
            logging.info(f"[EPISODIC] Добавлен эпизод: {reflection.get('summary', '')[:50]}")
        except Exception as e:
            logging.warning(f"[EPISODIC] Ошибка добавления эпизода: {e}")

    def recall(self, query: str, k: int = 3) -> list:
        """Ищет похожие эпизоды."""
        if self.index.ntotal == 0:
            return []

        try:
            q_emb = self.llm.embed(query)
            q_emb = q_emb / np.linalg.norm(q_emb)
            scores, idxs = self.index.search(q_emb.reshape(1, -1), min(k, self.index.ntotal))

            results = []
            for score, idx in zip(scores[0], idxs[0]):
                if idx < len(self.episodes) and score > 0.3:  # Порог релевантности
                    results.append({
                        "episode": self.episodes[idx],
                        "score": float(score)
                    })
            return results
        except Exception as e:
            logging.warning(f"[EPISODIC] Ошибка поиска: {e}")
            return []

    def get_insights(self) -> dict:
        """Собирает накопленные инсайты из всех эпизодов."""
        what_worked = set()
        what_to_avoid = set()
        user_preferences = set()

        for ep in self.episodes[-20:]:  # Последние 20 эпизодов
            ref = ep.get("reflection", {})
            if ref.get("what_worked") and ref["what_worked"] != "N/A":
                what_worked.add(ref["what_worked"])
            if ref.get("what_to_avoid") and ref["what_to_avoid"] != "N/A":
                what_to_avoid.add(ref["what_to_avoid"])
            if ref.get("user_preferences") and ref["user_preferences"] != "N/A":
                user_preferences.add(ref["user_preferences"])

        return {
            "what_worked": list(what_worked)[:5],
            "what_to_avoid": list(what_to_avoid)[:5],
            "user_preferences": list(user_preferences)[:5]
        }


class ProceduralMemory:
    """Хранит и обновляет правила поведения агента."""

    def __init__(self, llm, rules_path="procedural_memory.txt"):
        self.llm = llm
        self.rules_path = rules_path
        self.rules = self._load_rules()

    def _load_rules(self) -> str:
        """Загружает правила из файла."""
        if os.path.exists(self.rules_path):
            with open(self.rules_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return self._default_rules()

    def _default_rules(self) -> str:
        """Правила по умолчанию."""
        return """1. Отвечай кратко и по делу - пользователи ценят лаконичность
2. Учитывай контекст предыдущих сообщений - это помогает поддерживать связный диалог
3. Если не уверен - лучше уточни - это предотвращает недопонимание
4. Адаптируй стиль под собеседника - разные люди предпочитают разный стиль общения
5. Не повторяй информацию, которую уже сообщал - это раздражает"""

    def get_rules(self) -> str:
        """Возвращает текущие правила."""
        return self.rules

    def update_rules(self, insights: dict):
        """Обновляет правила на основе накопленного опыта."""
        if not any(insights.values()):
            return

        what_worked = "\n".join(f"- {x}" for x in insights.get("what_worked", []))
        what_to_avoid = "\n".join(f"- {x}" for x in insights.get("what_to_avoid", []))
        preferences = "\n".join(f"- {x}" for x in insights.get("user_preferences", []))

        prompt = [
            {"role": "system", "content": """Ты обновляешь правила поведения для AI-ассистента.
Объедини текущие правила с новыми инсайтами.
Выведи максимум 10 правил, каждое с кратким обоснованием.
Формат: [номер]. [Правило] - [Обоснование]
Отвечай ТОЛЬКО списком правил, без преамбулы."""},
            {"role": "user", "content": f"""Текущие правила:
{self.rules}

Что хорошо работало:
{what_worked or "Нет данных"}

Чего следует избегать:
{what_to_avoid or "Нет данных"}

Предпочтения пользователя:
{preferences or "Нет данных"}"""}
        ]

        try:
            new_rules = self.llm.chat(prompt, temperature=0.3)
            self.rules = new_rules.strip()
            self._save_rules()
            logging.info("[PROCEDURAL] Правила обновлены")
        except Exception as e:
            logging.warning(f"[PROCEDURAL] Ошибка обновления: {e}")

    def _save_rules(self):
        """Сохраняет правила в файл."""
        with open(self.rules_path, "w", encoding="utf-8") as f:
            f.write(self.rules)


class MemoryManager:
    """
    Менеджер памяти с четырьмя типами памяти (по модели agentic-memory):
    1. Working Memory (short_term) — текущий контекст разговора
    2. Episodic Memory — рефлексии прошлых разговоров
    3. Semantic Memory (long_term) — векторный поиск по всем сообщениям
    4. Procedural Memory — правила поведения агента
    """

    def __init__(
        self,
        persona_path,
        llm,
        short_term_limit=12,
        summarize_trigger=10,
        summary_keep_last=2,
        embedding_dim=768
    ):
        self.llm = llm
        self.short_term_limit = short_term_limit

        # 1. Working Memory — текущий контекст
        self.short_term = defaultdict(
            lambda: deque(maxlen=short_term_limit)
        )

        self.summaries = defaultdict(list)
        self.persona = self._load_persona(persona_path)

        # 2. Episodic Memory — рефлексии разговоров
        self.episodic = EpisodicMemory(llm, embedding_dim)

        # 3. Semantic Memory — два индекса
        self.embedding_dim = embedding_dim
        # Индекс для базы знаний (Notion и др.)
        self.knowledge_index = faiss.IndexFlatIP(embedding_dim)
        self.knowledge_texts = []
        # Индекс для сообщений чата (старый long_term)
        self.index = faiss.IndexFlatIP(embedding_dim)
        self.long_term_texts = []

        # 4. Procedural Memory — правила поведения
        self.procedural = ProceduralMemory(llm)

        self.summarize_trigger = summarize_trigger
        self.summary_keep_last = summary_keep_last

        # Счётчик сообщений для периодической рефлексии
        self.message_counts = defaultdict(int)

    def _load_persona(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
        
    def add(self, chat_id, role, content):
        self.short_term[chat_id].append({
            "role": role,
            "content": content
        })

        # Пишем в semantic memory (long-term)
        self._add_to_long_term(content, role)

        # Увеличиваем счётчик сообщений
        self.message_counts[chat_id] += 1

        # Каждые 10 сообщений делаем рефлексию для episodic memory
        if self.message_counts[chat_id] >= 10:
            self._create_episode(chat_id)
            self.message_counts[chat_id] = 0

    def _create_episode(self, chat_id):
        """Создаёт эпизод из текущего разговора."""
        messages = list(self.short_term[chat_id])
        if len(messages) < 4:
            return

        reflection = self.episodic.reflect_on_conversation(messages)
        if reflection:
            self.episodic.add_episode(chat_id, messages, reflection)

    def _summarize(self, chat_id):
        messages = list(self.short_term[chat_id])[:-self.summary_keep_last]

        if not messages:
            return
        
        text = "\n".join(
            f"{m['role']}: {m['content']}" for m in messages
        )

        prompt = [
            {"role": "system", "content": "Summarize the conversation briefly, preserving key facts and decisions"},
            {"role": "user", "content": text}
        ]

        summary = self.llm.chat(prompt, temperature=0.3)

        self.summaries[chat_id].append(summary)

        # оставляем только последние сообщения
        self.short_term[chat_id] = deque(
            list(self.short_term[chat_id]) [-self.summary_keep_last:],
            maxlen=self.short_term[chat_id].maxlen
        )

    def _add_to_long_term(self, text, role):
        """Добавляет в semantic memory (long-term)."""
        # Сохраняем только важные сообщения (достаточно длинные)
        if len(text) < 20:
            return

        try:
            embedding = self.llm.embed(text)
            # Нормализуем для косинусного сходства
            embedding = embedding / np.linalg.norm(embedding)
            self.index.add(embedding.reshape(1, -1))
            self.long_term_texts.append({"text": text, "role": role})
            logging.info(f"[SEMANTIC] Добавлено в long-term: {text[:40]}...")
        except Exception as e:
            logging.warning(f"[SEMANTIC] Ошибка при embedding: {e}")


    def search_knowledge(self, query, k=10):
        """Ищет в базе знаний (Notion и др.) — приоритетный поиск."""
        if self.knowledge_index.ntotal == 0:
            logging.info("[KNOWLEDGE] Индекс пуст")
            return []

        try:
            q_emb = self.llm.embed(query)
            q_emb = q_emb / np.linalg.norm(q_emb)
            scores, idxs = self.knowledge_index.search(q_emb.reshape(1, -1), min(k, self.knowledge_index.ntotal))

            logging.info(f"[KNOWLEDGE] Поиск '{query[:30]}...' — найдено {len(idxs[0])}, scores: {scores[0][:5]}")

            results = []
            for score, idx in zip(scores[0], idxs[0]):
                # Убрал порог — берём все результаты
                if idx < len(self.knowledge_texts):
                    item = self.knowledge_texts[idx]
                    results.append(item["text"])
            return results
        except Exception as e:
            logging.warning(f"[KNOWLEDGE] Ошибка поиска: {e}")
            return []

    def search_long_term(self, query, k=3):
        """Ищет в истории сообщений (вторичный поиск)."""
        if self.index.ntotal == 0:
            return []

        try:
            q_emb = self.llm.embed(query)
            q_emb = q_emb / np.linalg.norm(q_emb)
            scores, idxs = self.index.search(q_emb.reshape(1, -1), min(k, self.index.ntotal))

            results = []
            for score, idx in zip(scores[0], idxs[0]):
                if idx < len(self.long_term_texts) and score > 0.2:
                    item = self.long_term_texts[idx]
                    if isinstance(item, str):
                        results.append(item)
                    else:
                        results.append(item["text"])
            return results
        except Exception as e:
            logging.warning(f"[SEMANTIC] Ошибка поиска: {e}")
            return []

    def get_enriched_context(self, chat_id, query: str) -> dict:
        """
        Собирает обогащённый контекст из всех типов памяти.
        Возвращает dict с компонентами для system prompt.
        """
        context = {
            "persona": self.persona,
            "procedural_rules": self.procedural.get_rules(),
            "episodic_insights": None,
            "relevant_episodes": [],
            "semantic_recall": [],
            "working_memory": list(self.short_term[chat_id])
        }

        # Episodic: ищем похожие прошлые разговоры
        episodes = self.episodic.recall(query, k=2)
        if episodes:
            context["relevant_episodes"] = [
                ep["episode"]["reflection"] for ep in episodes
            ]

        # Episodic: общие инсайты
        insights = self.episodic.get_insights()
        if any(insights.values()):
            context["episodic_insights"] = insights

        # Knowledge: поиск в базе знаний (Notion) — приоритет
        knowledge_results = self.search_knowledge(query, k=10)  # Больше результатов
        context["knowledge_recall"] = knowledge_results

        # Semantic: релевантная история чатов (вторичный)
        semantic_results = self.search_long_term(query, k=3)
        context["semantic_recall"] = semantic_results

        return context
    
    def get_context(self, chat_id, query=None):
        messages = []

        # Объединяем все system-сообщения в одно
        system_content = self.persona

        if self.summaries[chat_id]:
            system_content += "\n\nConversation summary (for context only, do not repeat):\n"
            system_content += "\n".join(self.summaries[chat_id][-10:])

        if query:
            recalled = self.search_long_term(query, k=2)
            if recalled:
                system_content += "\n\nRelevant past information (use only if helpful, do not quote):\n"
                system_content += "\n".join(recalled)

        messages.append({
            "role": "system",
            "content": system_content
        })

        messages.extend(self.short_term[chat_id])

        return messages

    def load_knowledge(self, documents: list, source: str = "unknown"):
        """
        Загружает документы в Semantic Memory.

        Args:
            documents: Список dict с полями title, content
            source: Источник данных (для логирования)
        """
        logging.info(f"[SEMANTIC] load_knowledge вызван с {len(documents)} документами")

        from notion_loader import chunk_text
        logging.info("[SEMANTIC] chunk_text импортирован")

        # Сначала собираем все чанки
        all_chunks = []
        for i, doc in enumerate(documents):
            title = doc.get("title", "")
            content = doc.get("content", "")

            logging.info(f"[SEMANTIC] Обработка документа {i+1}/{len(documents)}: {title[:30]}... ({len(content)} символов)")

            if not content:
                continue

            chunks = chunk_text(content, chunk_size=1000, overlap=100)  # Больше контекста
            logging.info(f"[SEMANTIC]   -> {len(chunks)} чанков")

            for chunk in chunks:
                text_with_context = f"[{title}] {chunk}" if title else chunk
                all_chunks.append({
                    "text": text_with_context,
                    "title": title
                })

        total = len(all_chunks)
        logging.info(f"[SEMANTIC] Начинаю загрузку {total} чанков из {len(documents)} документов...")

        added = 0
        for i, chunk_data in enumerate(all_chunks):
            text = chunk_data["text"]
            title = chunk_data["title"]

            try:
                embedding = self.llm.embed(text)
                embedding = embedding / np.linalg.norm(embedding)
                # Записываем в ОТДЕЛЬНЫЙ индекс для знаний
                self.knowledge_index.add(embedding.reshape(1, -1))
                self.knowledge_texts.append({
                    "text": text,
                    "source": source,
                    "title": title
                })
                added += 1

                # Логируем прогресс каждые 10 чанков
                if (i + 1) % 10 == 0:
                    logging.info(f"[KNOWLEDGE] Прогресс: {i + 1}/{total} чанков")

            except Exception as e:
                logging.warning(f"[KNOWLEDGE] Ошибка чанка {i}: {e}")

        logging.info(f"[SEMANTIC] Готово! Загружено {added} чанков ({source})")
        return added

    def finalize_session(self):
        """Вызывается при завершении сессии — обновляет procedural memory."""
        insights = self.episodic.get_insights()
        if any(insights.values()):
            self.procedural.update_rules(insights)
            logging.info("[MEMORY] Procedural memory обновлена на основе опыта")

    def save(self, directory="memory_cache"):
        """Сохраняет все типы памяти на диск."""
        os.makedirs(directory, exist_ok=True)

        # Knowledge Base: FAISS индекс
        if self.knowledge_index.ntotal > 0:
            faiss.write_index(self.knowledge_index, os.path.join(directory, "knowledge_faiss.index"))
            with open(os.path.join(directory, "knowledge_texts.pkl"), "wb") as f:
                pickle.dump(self.knowledge_texts, f)

        # Chat History: FAISS индекс
        faiss.write_index(self.index, os.path.join(directory, "faiss.index"))
        with open(os.path.join(directory, "long_term_texts.pkl"), "wb") as f:
            pickle.dump(self.long_term_texts, f)

        # Episodic Memory
        faiss.write_index(self.episodic.index, os.path.join(directory, "episodic_faiss.index"))
        with open(os.path.join(directory, "episodes.pkl"), "wb") as f:
            pickle.dump(self.episodic.episodes, f)

        # Working Memory + summaries + счётчики
        data = {
            "summaries": {str(k): v for k, v in self.summaries.items()},
            "short_term": {str(k): list(v) for k, v in self.short_term.items()},
            "message_counts": {str(k): v for k, v in self.message_counts.items()}
        }
        with open(os.path.join(directory, "memory.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logging.info(f"[MEMORY] Сохранено: {self.knowledge_index.ntotal} knowledge, {self.index.ntotal} chat, {self.episodic.index.ntotal} episodic")

    def load(self, directory="memory_cache"):
        """Загружает все типы памяти с диска."""
        if not os.path.exists(directory):
            logging.info(f"[MEMORY] Папка {directory} не существует, начинаем с нуля")
            return

        logging.info(f"[MEMORY] Загружаю память из {directory}")

        # Knowledge Base: FAISS индекс
        knowledge_path = os.path.join(directory, "knowledge_faiss.index")
        if os.path.exists(knowledge_path):
            self.knowledge_index = faiss.read_index(knowledge_path)
            with open(os.path.join(directory, "knowledge_texts.pkl"), "rb") as f:
                self.knowledge_texts = pickle.load(f)
            logging.info(f"[KNOWLEDGE] Загружен индекс с {self.knowledge_index.ntotal} записями")

        # Chat History: FAISS индекс
        faiss_path = os.path.join(directory, "faiss.index")
        if os.path.exists(faiss_path):
            self.index = faiss.read_index(faiss_path)
            logging.info(f"[CHAT] Загружен индекс с {self.index.ntotal} записями")

        # Chat History: тексты
        texts_path = os.path.join(directory, "long_term_texts.pkl")
        if os.path.exists(texts_path):
            with open(texts_path, "rb") as f:
                self.long_term_texts = pickle.load(f)

        # Episodic Memory: FAISS индекс
        episodic_faiss_path = os.path.join(directory, "episodic_faiss.index")
        if os.path.exists(episodic_faiss_path):
            self.episodic.index = faiss.read_index(episodic_faiss_path)
            logging.info(f"[EPISODIC] Загружен индекс с {self.episodic.index.ntotal} эпизодами")

        # Episodic Memory: эпизоды
        episodes_path = os.path.join(directory, "episodes.pkl")
        if os.path.exists(episodes_path):
            with open(episodes_path, "rb") as f:
                self.episodic.episodes = pickle.load(f)
            logging.info(f"[EPISODIC] Загружено {len(self.episodic.episodes)} эпизодов")

        # Working Memory + summaries + счётчики
        memory_path = os.path.join(directory, "memory.json")
        if os.path.exists(memory_path):
            with open(memory_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Загружаем summaries
            self.summaries = defaultdict(list)
            for k, v in data.get("summaries", {}).items():
                try:
                    chat_id = int(k)
                except:
                    chat_id = k
                self.summaries[chat_id] = v

            # Загружаем short_term
            self.short_term = defaultdict(
                lambda: deque(maxlen=self.short_term_limit)
            )
            for k, v in data.get("short_term", {}).items():
                try:
                    chat_id = int(k)
                except:
                    chat_id = k
                self.short_term[chat_id] = deque(v, maxlen=self.short_term_limit)

            # Загружаем счётчики сообщений
            self.message_counts = defaultdict(int)
            for k, v in data.get("message_counts", {}).items():
                try:
                    chat_id = int(k)
                except:
                    chat_id = k
                self.message_counts[chat_id] = v

            logging.info(f"[WORKING] Загружено {len(self.short_term)} диалогов")
