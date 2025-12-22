import logging


class MessageHandler:
    """
    Обработчик сообщений с использованием AgenticMemory.
    AgenticMemory реализует 4 типа памяти:
    1. Working Memory — текущий контекст разговора
    2. Episodic Memory — похожие прошлые разговоры и инсайты
    3. Semantic Memory — база знаний (Notion)
    4. Procedural Memory — правила поведения
    """

    def __init__(self, memory):
        """
        Args:
            memory: AgenticMemory instance
        """
        self.memory = memory

    async def handle(self, chat_id, user_text):
        logging.info(f"[HANDLER] Получено сообщение: {user_text[:50]}")

        # Получаем полный контекст для LLM (включает все типы памяти)
        context = self.memory.get_context_for_llm(chat_id, user_text)

        logging.info(f"[HANDLER] Контекст: {len(context)} сообщений")

        # Вызываем LLM через AgenticMemory
        answer = self.memory.chat(context)

        logging.info(f"[HANDLER] Ответ: {answer[:50]}")

        # Сохраняем в working memory
        self.memory.add_message(chat_id, "user", user_text)
        self.memory.add_message(chat_id, "assistant", answer)

        return answer
