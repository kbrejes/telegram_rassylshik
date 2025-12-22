import logging
import os
from dotenv import load_dotenv
from telethon import TelegramClient, events

load_dotenv()

from agentic_memory import AgenticMemory
from handler import MessageHandler


def load_notion_knowledge(memory):
    """Загружает знания из Notion в Semantic Memory (Weaviate knowledge_base)."""
    notion_api_key = os.getenv("NOTION_API_KEY")
    notion_database_id = os.getenv("NOTION_DATABASE_ID")  # Опционально
    notion_page_ids = os.getenv("NOTION_PAGE_IDS")  # Опционально, через запятую

    if not notion_api_key:
        logging.info("[NOTION] NOTION_API_KEY не задан, пропускаем загрузку")
        return

    try:
        from notion_loader import NotionLoader, chunk_text
        loader = NotionLoader(notion_api_key)

        documents = []

        # Загрузка из конкретной базы данных
        if notion_database_id:
            logging.info(f"[NOTION] Загружаю из базы данных: {notion_database_id}")
            db_docs = loader.load_database(notion_database_id)
            documents.extend(db_docs)

        # Загрузка конкретных страниц (рекурсивно с вложенными)
        if notion_page_ids:
            page_ids = [pid.strip() for pid in notion_page_ids.split(",")]
            for page_id in page_ids:
                logging.info(f"[NOTION] Загружаю страницу с вложенными: {page_id}")
                pages = loader.load_page_recursive(page_id, max_depth=5)
                documents.extend(pages)

        # Если ничего не указано — загружаем весь workspace
        if not notion_database_id and not notion_page_ids:
            logging.info("[NOTION] Загружаю все доступные страницы из workspace")
            documents = loader.load_workspace_pages(limit=50)

        if documents:
            logging.info(f"[NOTION] Обрабатываю {len(documents)} документов...")

            for doc in documents:
                title = doc.get("title", "Untitled")
                content = doc.get("content", "")
                page_id = doc.get("id", "unknown")

                if not content.strip():
                    continue

                # Разбиваем на чанки
                chunks = chunk_text(content, chunk_size=1000, overlap=100)

                # Добавляем в Weaviate knowledge_base
                memory.add_knowledge(
                    chunks=chunks,
                    title=title,
                    source=f"notion:{page_id}"
                )

            logging.info("[NOTION] Данные загружены в Weaviate knowledge_base")
        else:
            logging.warning("[NOTION] Нет документов для загрузки")

    except Exception as e:
        logging.error(f"[NOTION] Ошибка загрузки: {e}", exc_info=True)


logging.basicConfig(level=logging.INFO)

# --- Telegram ---
api_id = os.getenv("API_ID")
api_hash = os.getenv("API_HASH")
base_url = os.getenv("BASE_URL")
model = os.getenv("MODEL")

client = TelegramClient('agent_bot', api_id, api_hash)

# --- AgenticMemory (Weaviate + LangChain) ---
memory = AgenticMemory(
    llm_base_url=base_url,
    llm_model=model,
    persona_path="persona.txt",
    procedural_path="procedural_memory.txt",
    weaviate_host="localhost",
    weaviate_port=8080,
    short_term_limit=12,
)

handler = MessageHandler(memory)


@client.on(events.NewMessage(incoming=True))
async def on_message(event):
    if not event.message.text:
        return

    logging.info(f"[MAIN] Новое входящее сообщение: {event.message.text[:50]}")

    chat_id = event.chat_id
    text = event.message.text

    reply = await handler.handle(chat_id, text)
    logging.info(f"[MAIN] Отправляю ответ: {reply[:50]}")
    await event.reply(reply)


if __name__ == "__main__":
    logging.info("Starting bot with AgenticMemory (Weaviate + LangChain)...")
    logging.info("  - Working Memory: short-term context (deque)")
    logging.info("  - Episodic Memory: conversation reflections (Weaviate hybrid search)")
    logging.info("  - Semantic Memory: knowledge base (Weaviate hybrid search)")
    logging.info("  - Procedural Memory: behavioral rules (file-based)")

    # Загружаем знания из Notion (если настроено)
    load_notion_knowledge(memory)

    try:
        with client:
            client.run_until_disconnected()
    finally:
        logging.info("Finalizing session...")
        # Финализируем последнюю сессию (сохраняем episodic + обновляем procedural)
        # Здесь нужен chat_id — пропускаем, так как у нас может быть несколько чатов
        logging.info("Closing Weaviate connection...")
        memory.close()
