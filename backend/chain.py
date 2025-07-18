import os
import sys
import boto3
import tempfile
import io
import json
import regex as re
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain.prompts import PromptTemplate
from langchain.chains.llm import LLMChain
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS

load_dotenv()

s3_client = boto3.client('s3')

def load_index(index_path=None, bucket="herewegopt", file="index.faiss", pickle="index.pkl"):
    if index_path is None:
        response = s3_client.get_object(Bucket=bucket, Key=file)
        data = response['Body'].read()

        response_pickle = s3_client.get_object(Bucket=bucket, Key=pickle)
        pickle_data = response_pickle['Body'].read()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_index_path = os.path.join(temp_dir, "index.faiss")
            temp_pickle_path = os.path.join(temp_dir, "index.pkl")

            with open(temp_index_path, 'wb') as f:
                f.write(data)
            with open(temp_pickle_path, 'wb') as f:
                f.write(pickle_data)

            embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
            faiss_index = FAISS.load_local(temp_dir, embeddings, allow_dangerous_deserialization=True)
    else:
        embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
        faiss_index = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

    return faiss_index

def get_tweets(entity: str, k: int = 20):
    faiss_index = load_index()
    docs = faiss_index.similarity_search(entity, k=k)
    filtered = [
        (doc.page_content, doc.metadata.get("date", "Unknown"))
        for doc in docs
        if entity.lower() in doc.page_content.lower()
        or entity.lower() in [kw.lower() for kw in doc.metadata.get("keywords", [])]
    ]
    return filtered

def format(rows):
    if not rows:
        return "No tweets found"
    context = ""
    for text, date in rows:
        context += f"[{date}] {text}\n\n"
    return context

def generate_summary(entity: str, context: str) -> str:
    llm = init_chat_model("gpt-4o-mini", model_provider="openai")
    prompt = PromptTemplate(
        input_variables=["entity", "context"],
        template=(
            "You are a football analyst.\n\n"
            "Given the following tweets about {entity}, summarize their current situation. "
            "Focus on any recent transfer rumors, injuries, management changes, or rumors. "
            "Give the summary in easily readable bullet points, only giving a sentence per point."
            "Make the section at most four buller points long.\n\n"
            "Tweets:\n"
            "{context}\n"
            "Summary:"
        )
    )
    chain = LLMChain(llm=llm, prompt=prompt)
    return chain.run({"entity": entity, "context": context})

def generate_timeline(tweets: list[tuple[str, str]]):
    if not tweets:
        return []
    
    llm = init_chat_model("gpt-3.5-turbo", model_provider="openai")
    prompt = PromptTemplate(
        input_variables=["tweets"],
        template=(
            "Write the summaries as bullet points starting with a dash and a space,\n"
            "then the date in MM/DD/YYYY format, followed by a space and the summary.\n"
            "Example:\n"
            "- 05/31/2025 Player X signed for Club Y.\n"
            "- 05/30/2025 Manager Z announced retirement.\n"
            "Tweets:\n{tweets}\n\nSummaries:"
        )
    )
    recent = tweets[-10:]
    tweet_text = ""
    for i, (date, text) in enumerate(recent, 1):
        tweet_text += f"Tweet {i} [{date}]: {text}\n\n"

    chain = LLMChain(llm=llm, prompt=prompt)
    response = chain.run({"tweets": tweet_text})

    timeline = []
    for line in response.strip().split("\n"):
        match = re.match(r'-\s*(\d{2}/\d{2}/\d{4})\s+(.*)', line)
        if match:
            date, summary = match.groups()
            timeline.append({ "date": date.strip(), "summary": summary.strip() })

    return timeline


if __name__ == "__main__":
    if len(sys.argv) > 1:
        entity = " ".join(sys.argv[1:]).strip()
    else:
        print(json.dumps({ "error": "No entity provided" }))
        sys.exit(1)

    tweets = get_tweets(entity)
    context = format(tweets)
    if context == "No tweets found":
        print(json.dumps({ "summary": context, "tweets": [] }, ensure_ascii=False))
    else:
        summary = generate_summary(entity, context)
        timeline = generate_timeline(tweets)
        print(json.dumps({
            "summary": summary,
            "timeline": timeline
        }, ensure_ascii=False))