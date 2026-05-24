"""
ingest.py
- data/ 폴더의 pdf, txt 파일을 읽어서
- 청크로 자른 뒤
- OpenAI 임베딩으로 Chroma 벡터DB를 만든다.
"""

from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

from config import DATA_DIR, DB_DIR, OPENAI_API_KEY, EMBEDDING_MODEL


def load_documents():
    """data/ 폴더에서 pdf, txt 파일 로드"""
    if not DATA_DIR.exists():
        raise FileNotFoundError(f"DATA_DIR가 없습니다: {DATA_DIR}")

    docs = []
    for path in DATA_DIR.glob("*"):
        if path.suffix.lower() == ".pdf":
            loader = PyPDFLoader(str(path))
        elif path.suffix.lower() in [".txt", ".md"]:
            loader = TextLoader(str(path), encoding="utf-8")
        else:
            # 기타 확장자는 무시
            continue

        file_docs = loader.load()
        # 메타데이터에 source 파일명만 간단히 넣어두기
        for d in file_docs:
            d.metadata.setdefault("source", path.name)
        docs.extend(file_docs)

    if not docs:
        raise ValueError("data/ 폴더에서 불러온 문서가 없습니다.")

    print(f"✅ 문서 개수: {len(docs)}")
    return docs


def split_documents(docs):
    """문서를 청크로 분할"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", " ", ""],
    )
    chunks = splitter.split_documents(docs)
    print(f"✅ 청크 개수: {len(chunks)}")
    return chunks


def build_vectorstore(chunks):
    """청크를 임베딩해서 Chroma 벡터DB 생성"""
    if not OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

    embeddings = OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        openai_api_key=OPENAI_API_KEY,
    )

    DB_DIR.mkdir(parents=True, exist_ok=True)

    vectordb = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(DB_DIR),
    )
    vectordb.persist()
    print(f"✅ Chroma 벡터DB 저장 완료: {DB_DIR}")
    return vectordb


def main():
    docs = load_documents()
    chunks = split_documents(docs)
    build_vectorstore(chunks)


if __name__ == "__main__":
    main()
