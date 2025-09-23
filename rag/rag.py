import logging
import os
import tempfile
from typing import List

import boto3
import dotenv
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters.character import RecursiveCharacterTextSplitter

dotenv.load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


class RAG:
    def __init__(self):
        self.REQUIRED_VARS = {
            "S3_ENDPOINT": os.getenv("S3_ENDPOINT"),
            "S3_ACCESS_KEY": os.getenv("S3_ACCESS_KEY"),
            "S3_SECRET_KEY": os.getenv("S3_SECRET_KEY"),
            "S3_BUCKET": os.getenv("S3_BUCKET"),
        }
        self.local_files = []
        self.embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )

        self.download_from_s3()

    def validate_environment_variables(self):
        """Проверка наличия обязательных переменных окружения"""
        for var_name, value in self.REQUIRED_VARS.items():
            if not value or value.strip().lower() == "none":
                raise ValueError(f"{var_name} не задан. Проверьте .env.")

    def load_and_index_documents(self, documents: List[str] = []):
        # пустой список для всех чанков
        all_chunks = []

        for path in documents:
            if path.endswith(".pdf"):
                loader = PyPDFLoader(path)
            elif path.endswith(".txt"):
                loader = TextLoader(path, encoding="utf-8")
            else:
                continue
            loaded = loader.load()

            valid_docs = [
                doc
                for doc in loaded
                if hasattr(doc, "page_content")
                and isinstance(doc.page_content, str)
                and doc.page_content.strip()
            ]

            for doc in valid_docs:
                doc.metadata["source"] = os.path.basename(path)

            if not valid_docs:
                continue

            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000, chunk_overlap=50, separators=["\n\n", "\n", " ", ""]
            )
            chunks = text_splitter.split_documents(valid_docs)
            all_chunks.extend(chunks)

        if not all_chunks:
            print("Не найдено контента для индексации. Создан пустой индекс.")
            all_chunks = [
                Document(
                    page_content="Нет доступных документов для поиска.", metadata={}
                )
            ]

        vectorstore = FAISS.from_documents(all_chunks, self.embeddings)
        vectorstore.save_local("./vectorstore_faiss", index_name="index")
        print(
            f"""Индекс успешно создан и сохранен.
              Проиндексировано чанков: {len(all_chunks)}"""
        )

    def download_from_s3(self):
        # Проверка подключения - валидация переменных окружения
        self.validate_environment_variables()
        logger.info("Environment variables validation successful")

        s3 = boto3.client(
            "s3",
            endpoint_url=self.REQUIRED_VARS["S3_ENDPOINT"],
            aws_access_key_id=self.REQUIRED_VARS["S3_ACCESS_KEY"],
            aws_secret_access_key=self.REQUIRED_VARS["S3_SECRET_KEY"],
            region_name="ru-central1",
        )

        try:
            objects = s3.list_objects_v2(Bucket=self.REQUIRED_VARS["S3_BUCKET"])
        except Exception as e:
            print(f"Ошибка подключения: {e}")
            return None

        if "Contents" not in objects:
            return self.load_and_index_documents([])

        with tempfile.TemporaryDirectory() as tmpdir:
            for obj in objects["Contents"]:
                key = obj.get("Key")
                if not key or not isinstance(key, str) or key.endswith("/"):
                    continue

                size = obj.get("Size", 0)
                if size == 0:
                    continue

                local_path = os.path.join(tmpdir, os.path.basename(key))
                try:
                    s3.download_file(self.REQUIRED_VARS["S3_BUCKET"], key, local_path)
                    if os.path.getsize(local_path) == 0:
                        continue
                    self.local_files.append(local_path)
                except Exception as e:
                    print(f"Ошибка скачивания {key}: {e}")
                    continue

            self.load_and_index_documents(self.local_files)

    def search_engine(self, current_user_input: str = ""):
        if not os.path.exists("./vectorstore_faiss"):
            print(
                """Ошибка: папка с индексом
            './vectorstore_faiss' не найдена. Запустите индексацию."""
            )
            return ""

        vectorstore = FAISS.load_local(
            "./vectorstore_faiss", self.embeddings, allow_dangerous_deserialization=True
        )
        retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

        retrieved_docs = retriever.invoke(current_user_input)

        if retrieved_docs:
            sources = {
                doc.metadata.get("source", "Неизвестно") for doc in retrieved_docs
            }
            print(
                f"""RAG: найдено {len(retrieved_docs)} релевантных \
                  фрагментов из источников: {list(sources)}"""
            )
            context_chunks = "\n\n---\n\n".join(
                [doc.page_content for doc in retrieved_docs]
            )
            return context_chunks
        print("RAG: релевантных фрагментов не найдено.")
        return ""


# Создаем экземпляр РАГа
# rag = RAG()
# print(rag.search_engine("Базовые методы защиты LLM"))
# print KAPIBARA
