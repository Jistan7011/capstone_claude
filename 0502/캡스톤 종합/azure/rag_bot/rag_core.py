"""
rag_core.py
- Chroma 벡터DB를 로드해서
- 질문에 답하는 RAG 체인 구성
"""

import os

from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_classic.chains import RetrievalQA
from langchain_core.prompts import ChatPromptTemplate

from config import DB_DIR, OPENAI_API_KEY, EMBEDDING_MODEL, CHAT_MODEL


SYSTEM_PROMPT = """
너는 딸기 병해 관리 에이전트야.
벡터DB에서 검색된 문서 내용을 바탕으로 대답하되 인터넷 검색도 허용함.

[답변 형식 규칙]

1. 말투:
- 보고서처럼 작성.
- 인사말, 개요, 결론 문단 금지.
- "죄송하지만", "요약하면" 같은 불필요한 문장 넣지 말 것.

2. 출력 형식(항상 이 순서와 제목 사용):
### 오늘 바로 해야 할 일
- 항목 1 (한 줄, 구체적인 행동)
- 항목 2
- 항목 3
...

### 7일 관리 계획
- 1~3일차: ...
- 4~5일차: ...
- 6~7일차: ...
### 환경 조건(온도/습도 등)에 따른 관리법도 포함.
- 몇도에서 몇도 유지
- 습도 몇 % 이상/이하 유지
- 토양 수분 관리법 등
### 약제 사용방법
- 약제명(상품명 아님), 희석배수, 살포시기(아침/저녁 등), 살포 간격 등 구체적으로 하지만 간결하게.

### 주의사항
- 농약 관련 문장은 "딸기에 등록된 농약을 라벨 기준에 맞게 사용"처럼
  원칙만 말하고, 상품명/정확 희석배수는 말하지 않는다.

3. 기타:
- 한 항목은 최대 1~2문장 이내로 짧게.
- 전체 답변은 25줄 이내로 제한한다.
"""


QA_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        (
            "human",
            "질문: {question}\n\n"
            "아래는 참고할 수 있는 문서 내용이야:\n"
            "{context}\n\n"
            "위 내용을 바탕으로 한국어로 답변해줘.",
        ),
    ]
)


def get_vectordb():
    """이미 만들어진 Chroma 벡터DB 로드"""
    if not DB_DIR.exists():
        raise FileNotFoundError(
            f"벡터DB 폴더가 없습니다: {DB_DIR}\n"
            "먼저 ingest.py 를 실행해서 DB를 만들어 주세요."
        )

    if not OPENAI_API_KEY:
        raise EnvironmentError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")

    embeddings = OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        openai_api_key=OPENAI_API_KEY,
    )

    vectordb = Chroma(
        embedding_function=embeddings,
        persist_directory=str(DB_DIR),
    )
    return vectordb


def get_retriever(k: int = 4):
    vectordb = get_vectordb()
    retriever = vectordb.as_retriever(search_kwargs={"k": k})
    return retriever


def get_qa_chain():
    retriever = get_retriever()
    llm = ChatOpenAI(
        model=CHAT_MODEL,
        temperature=0.2,
        openai_api_key=OPENAI_API_KEY,
    )

    chain = RetrievalQA.from_chain_type(
        llm=llm,
        retriever=retriever,
        chain_type="stuff",
        chain_type_kwargs={
            "prompt": QA_PROMPT,
        },
        return_source_documents=True,  # 🔥 디버그용으로 True로
    )
    return chain


def ask(question: str, is_first: bool = False, history: list = None) -> str:
    """
    - 첫 질문(is_first=True): 병해관리 RAG 형식 답변
    - 이후 질문: 딸기 병해 전문가 tone + 문맥 유지 + 간단 답변
    """

    if history is None:
        history = []

    try:
        # ================================
        #   1) 첫 질문 → RAG 상세 보고서
        # ================================
        if is_first:
            qa_chain = get_qa_chain()
            result = qa_chain.invoke({"query": question})
            return result["result"]

        # ===========================================
        #   2) 이후 질문 → 병해 전문가 간단 GPT 모드
        # ===========================================
        else:
            llm = ChatOpenAI(
                model=CHAT_MODEL,
                temperature=0.4,
                openai_api_key=OPENAI_API_KEY,
            )

            # --- 이전 대화 내용을 문자열로 변환 ---
            history_text = "\n".join(
                [f"{msg['role']}: {msg['content']}" for msg in history]
            )

            simple_prompt = f"""
            너는 딸기 병해 관리 전문가야.
            항상 딸기 병해·환경·관리·예방을 기준으로 답하고,
            일반적인 식물 조언이 아니라 딸기 기준으로만 대답해.

            이전 대화:
            {history_text}

            사용자의 질문:
            {question}

            답변 규칙:
            - 1~2문장으로 짧게
            - 병해, 온도/습도, 통풍, 잎/과육 증상 등 맥락 기반으로 답변
            - 전문적이지만 너무 길면 안 됨

            이 규칙에 따라 정확하고 짧게 답해줘.
            """

            response = llm.invoke(simple_prompt)
            return response.content

    except Exception as e:
        return f"⚠️ 오류 발생: {e}"


if __name__ == "__main__":
    # 터미널 테스트용
    while True:
        q = input("질문 (종료: 엔터만 치기) > ").strip()
        if not q:
            break
        print("---- 답변 ----")
        print(ask(q))
        print("--------------\n")
