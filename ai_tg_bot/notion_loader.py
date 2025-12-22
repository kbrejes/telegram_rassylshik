"""
Загрузчик данных из Notion для Semantic Memory.
Поддерживает загрузку страниц и баз данных.
"""

import logging
import os
from typing import List, Dict, Optional
from notion_client import Client


class NotionLoader:
    """Загружает контент из Notion для использования в Semantic Memory."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("NOTION_API_KEY")
        if not self.api_key:
            raise ValueError("NOTION_API_KEY не найден в переменных окружения")

        self.client = Client(auth=self.api_key)
        logging.info("[NOTION] Клиент инициализирован")

    def _extract_text_from_block(self, block: dict) -> str:
        """Извлекает текст из блока Notion."""
        block_type = block.get("type")

        if not block_type or block_type not in block:
            return ""

        block_content = block[block_type]

        # Типы блоков с rich_text
        if "rich_text" in block_content:
            texts = []
            for text_obj in block_content["rich_text"]:
                texts.append(text_obj.get("plain_text", ""))
            return " ".join(texts)

        # Заголовки
        if block_type in ["heading_1", "heading_2", "heading_3"]:
            texts = []
            for text_obj in block_content.get("rich_text", []):
                texts.append(text_obj.get("plain_text", ""))
            return " ".join(texts)

        # Списки
        if block_type in ["bulleted_list_item", "numbered_list_item"]:
            texts = []
            for text_obj in block_content.get("rich_text", []):
                texts.append(text_obj.get("plain_text", ""))
            return "- " + " ".join(texts)

        # Чекбоксы
        if block_type == "to_do":
            texts = []
            for text_obj in block_content.get("rich_text", []):
                texts.append(text_obj.get("plain_text", ""))
            checked = "✓" if block_content.get("checked") else "○"
            return f"{checked} " + " ".join(texts)

        # Код
        if block_type == "code":
            texts = []
            for text_obj in block_content.get("rich_text", []):
                texts.append(text_obj.get("plain_text", ""))
            lang = block_content.get("language", "")
            return f"```{lang}\n" + " ".join(texts) + "\n```"

        # Цитаты
        if block_type == "quote":
            texts = []
            for text_obj in block_content.get("rich_text", []):
                texts.append(text_obj.get("plain_text", ""))
            return "> " + " ".join(texts)

        # Разделитель
        if block_type == "divider":
            return "---"

        return ""

    def _get_page_content(self, page_id: str, child_pages: list = None) -> str:
        """
        Получает весь контент страницы.

        Args:
            page_id: ID страницы
            child_pages: Список для сбора ID вложенных страниц (мутируется)
        """
        blocks = []
        cursor = None

        while True:
            response = self.client.blocks.children.list(
                block_id=page_id,
                start_cursor=cursor
            )

            for block in response.get("results", []):
                block_type = block.get("type")

                # Вложенная страница — запоминаем ID для отдельной загрузки
                if block_type == "child_page" and child_pages is not None:
                    child_pages.append(block["id"])
                    continue

                # Ссылка на другую страницу
                if block_type == "link_to_page" and child_pages is not None:
                    link_data = block.get("link_to_page", {})
                    if link_data.get("type") == "page_id":
                        child_pages.append(link_data["page_id"])
                    continue

                text = self._extract_text_from_block(block)
                if text:
                    blocks.append(text)

                # Рекурсивно получаем дочерние блоки (но не страницы)
                if block.get("has_children") and block_type != "child_page":
                    child_content = self._get_page_content(block["id"], child_pages)
                    if child_content:
                        blocks.append(child_content)

            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")

        return "\n".join(blocks)

    def _get_page_title(self, page: dict) -> str:
        """Извлекает заголовок страницы."""
        properties = page.get("properties", {})

        # Ищем свойство Title или Name
        for prop_name in ["Title", "Name", "title", "name"]:
            if prop_name in properties:
                prop = properties[prop_name]
                if prop.get("type") == "title":
                    title_arr = prop.get("title", [])
                    if title_arr:
                        return title_arr[0].get("plain_text", "Без названия")

        return "Без названия"

    def load_page(self, page_id: str, collect_children: bool = False) -> Dict:
        """
        Загружает одну страницу из Notion.

        Args:
            page_id: ID страницы Notion
            collect_children: Собирать ли ID вложенных страниц

        Returns:
            Dict с title, content, url, child_pages (если collect_children=True)
        """
        try:
            page = self.client.pages.retrieve(page_id)
            title = self._get_page_title(page)

            child_pages = [] if collect_children else None
            content = self._get_page_content(page_id, child_pages)
            url = page.get("url", "")

            logging.info(f"[NOTION] Загружена страница: {title}")

            result = {
                "title": title,
                "content": content,
                "url": url,
                "id": page_id
            }

            if collect_children:
                result["child_pages"] = child_pages

            return result
        except Exception as e:
            logging.error(f"[NOTION] Ошибка загрузки страницы {page_id}: {e}")
            return None

    def load_page_recursive(self, page_id: str, max_depth: int = 5) -> List[Dict]:
        """
        Рекурсивно загружает страницу и все вложенные страницы.

        Args:
            page_id: ID родительской страницы
            max_depth: Максимальная глубина вложенности

        Returns:
            Список всех загруженных страниц
        """
        all_pages = []
        visited = set()

        def _load_recursive(pid: str, depth: int):
            if depth > max_depth or pid in visited:
                return

            visited.add(pid)
            page_data = self.load_page(pid, collect_children=True)

            if not page_data:
                return

            # Добавляем страницу (без child_pages в результат)
            child_pages = page_data.pop("child_pages", [])
            if page_data["content"]:
                all_pages.append(page_data)

            # Рекурсивно загружаем вложенные
            for child_id in child_pages:
                _load_recursive(child_id, depth + 1)

        _load_recursive(page_id, 0)
        logging.info(f"[NOTION] Рекурсивно загружено {len(all_pages)} страниц")
        return all_pages

    def load_database(self, database_id: str, limit: int = 100) -> List[Dict]:
        """
        Загружает все страницы из базы данных Notion.

        Args:
            database_id: ID базы данных Notion
            limit: Максимальное количество страниц

        Returns:
            Список Dict с title, content, url
        """
        pages = []
        cursor = None
        count = 0

        try:
            while count < limit:
                response = self.client.databases.query(
                    database_id=database_id,
                    start_cursor=cursor,
                    page_size=min(100, limit - count)
                )

                for page in response.get("results", []):
                    page_data = self.load_page(page["id"])
                    if page_data and page_data["content"]:
                        pages.append(page_data)
                        count += 1

                if not response.get("has_more") or count >= limit:
                    break
                cursor = response.get("next_cursor")

            logging.info(f"[NOTION] Загружено {len(pages)} страниц из базы данных")
            return pages

        except Exception as e:
            logging.error(f"[NOTION] Ошибка загрузки базы данных {database_id}: {e}")
            return pages

    def load_workspace_pages(self, limit: int = 50) -> List[Dict]:
        """
        Загружает все доступные страницы из workspace.

        Args:
            limit: Максимальное количество страниц

        Returns:
            Список Dict с title, content, url
        """
        pages = []
        cursor = None
        count = 0

        try:
            while count < limit:
                response = self.client.search(
                    filter={"property": "object", "value": "page"},
                    start_cursor=cursor,
                    page_size=min(100, limit - count)
                )

                for page in response.get("results", []):
                    page_data = self.load_page(page["id"])
                    if page_data and page_data["content"]:
                        pages.append(page_data)
                        count += 1

                if not response.get("has_more") or count >= limit:
                    break
                cursor = response.get("next_cursor")

            logging.info(f"[NOTION] Загружено {len(pages)} страниц из workspace")
            return pages

        except Exception as e:
            logging.error(f"[NOTION] Ошибка загрузки workspace: {e}")
            return pages


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """
    Разбивает текст на чанки для эмбеддингов.

    Args:
        text: Исходный текст
        chunk_size: Размер чанка в символах
        overlap: Перекрытие между чанками

    Returns:
        Список чанков
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Ищем конец предложения или абзаца (только если не конец текста)
        if end < len(text):
            best_break = None
            for sep in ["\n\n", "\n", ". ", "! ", "? "]:
                pos = text.rfind(sep, start + chunk_size // 2, end)  # Ищем во второй половине чанка
                if pos > start:
                    best_break = pos + len(sep)
                    break
            if best_break:
                end = best_break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        # Двигаемся вперёд, overlap только если есть куда
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)  # Гарантируем движение вперёд

    return chunks
