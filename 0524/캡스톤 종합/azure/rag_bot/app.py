import streamlit as st
from rag_core import ask

st.set_page_config(page_title="병해관리 에이전트", layout="wide")

# --- CSS (카카오톡 말풍선 스타일) ---
st.markdown("""
    <style>
    .user-bubble {
        background-color: #a8e3ff;
        color: black;
        padding: 10px 15px;
        border-radius: 15px;
        margin: 5px 0;
        max-width: 70%;
        float: right;
        clear: both;
    }
    .assistant-container {
        display: flex;
        align-items: flex-start;
        margin: 10px 0;
        clear: both;
    }
    .assistant-profile {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        margin-right: 10px;
    }
    .assistant-bubble {
        background-color: #ffffff;
        color: black;
        padding: 10px 15px;
        border-radius: 15px;
        border: 1px solid #ccc;
        max-width: 70%;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🍓 병해관리 에이전트")

# 세션 저장
if "messages" not in st.session_state:
    st.session_state["messages"] = []


# --- 기존 메시지 출력 ---
for msg in st.session_state["messages"]:
    if msg["role"] == "user":
        # 내 말풍선(오른쪽)
        st.markdown(f"""
            <div class="user-bubble">{msg['content']}</div>
        """, unsafe_allow_html=True)

    else:
        # 에이전트 말풍선(왼쪽 + 프로필 이미지)
        st.markdown(f"""
            <div class="assistant-container">
                <img class="assistant-profile" src="https://i.ibb.co/3phvbnN/strawberry-icon.png">
                <div class="assistant-bubble">{msg['content']}</div>
            </div>
        """, unsafe_allow_html=True)


# 질문 횟수 저장
if "question_count" not in st.session_state:
    st.session_state["question_count"] = 0

user_input = st.chat_input("메시지를 입력하세요...")

if user_input:
    # 내 메시지 저장
    st.session_state["messages"].append({"role": "user", "content": user_input})

    # UI 출력 (말풍선)
    st.markdown(f"""
        <div class="user-bubble">{user_input}</div>
    """, unsafe_allow_html=True)

    # ------ 여기에서 첫 질문인지 확인 ------
    is_first = (st.session_state["question_count"] == 0)

    # RAG or GPT 분기
    answer = ask(
    user_input,
    is_first=is_first,
    history=st.session_state["messages"]
    )


    # 질문 횟수 +1
    st.session_state["question_count"] += 1

    # 에이전트 메시지 저장
    st.session_state["messages"].append({"role": "assistant", "content": answer})

    # UI 출력 (카톡 스타일)
    st.markdown(f"""
        <div class="assistant-container">
            <img class="assistant-profile" src="https://i.ibb.co/3phvbnN/strawberry-icon.png">
            <div class="assistant-bubble">{answer}</div>
        </div>
    """, unsafe_allow_html=True)
    