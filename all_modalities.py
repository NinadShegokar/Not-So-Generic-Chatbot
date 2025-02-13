import nltk
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')
    
from nltk.tokenize import sent_tokenize
import os
import torch
import requests
import faiss
import numpy as np
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from transformers import AutoTokenizer, AutoModel
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi
import fitz
from groq import Groq
import base64
from pathlib import Path

GROQ_API_KEY = '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'
client = Groq(api_key=GROQ_API_KEY)

llava_model = "llava-v1.5-7b-4096-preview"
llama_model = "llama3-groq-8b-8192-tool-use-preview"

def encode_to_64(image_path):
    with open("rag_image.jpg", 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')
    
def image_to_text(client, model, base64_image, prompt):
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}",
                        },
                    },
                ],
            }
        ],
        model=model
    )
    return chat_completion.choices[0].message.content

def further_query(client, image_description, user_prompt):
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": """
                    You are an image description chatbot.
                    A short description of an image will be provided to you.
                    Answer the questions asked, based on that description.
                    If the description does not provide any context regarding the query asked, say that 'I could not extract this detail from the image'.
                """
            },
            {
                "role": "user",
                "content": f"{image_description}\n\n{user_prompt}"
            }
        ],
        model=llama_model
    )
    return chat_completion.choices[0].message.content

def complete_image_func(client, image_path, model, user_prompt):
    base64_image = encode_to_64(image_path)
    prompt = "Describe the image"
    image_description = image_to_text(client, model, base64_image, prompt)
    return further_query(client, image_description, user_prompt)


# my_finalprompt = "What do you think the people in the image are doing?"
# print(final_func(client, "images/image6.jpg", llava_model, my_finalprompt))


base_dir = 'extracted_images/'
pdf_dir = os.path.join(base_dir, 'pdfs')
web_dir = os.path.join(base_dir, 'webscraping')

os.makedirs(pdf_dir, exist_ok=True)
os.makedirs(web_dir, exist_ok=True)

def extract_text_and_images_from_pdf(pdf_file):
    """Extract text and images from a PDF file and save images to the designated directory."""
    doc = fitz.open("rag_document.pdf")
    all_text = ""
    
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)

        page_text = page.get_text("text")
        all_text += f"--- Page {page_num + 1} ---\n{page_text}\n"

        images = page.get_images(full=True)
        for img_index, img in enumerate(images):
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]  
            img_filename = f"{pdf_dir}/image_page{page_num+1}_{img_index}.{image_ext}"
            with open(img_filename, "wb") as img_file:
                img_file.write(image_bytes)
            # print(f"Saved PDF image: {img_filename}")

    return all_text


def scrape_page(url, web_dir):
    """Scrape visible text and images from a webpage and save only .jpg, .jpeg, and .png images."""
    r = requests.get(url)
    if r.status_code == 200:
        soup = BeautifulSoup(r.content, 'html.parser')
        
        for element in soup(['script', 'style']):
            element.extract()

        all_text = soup.get_text(separator=' ')
        clean_text = ' '.join(all_text.split())

        images = soup.find_all('img')
        image_urls = []
        
        os.makedirs(web_dir, exist_ok=True)
        
        for img in images:
            img_url = img.get('src')
            full_img_url = urljoin(url, img_url)  

            if any(full_img_url.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png']):
                image_urls.append(full_img_url)

                try:
                    img_response = requests.get(full_img_url)
                    img_name = os.path.join(web_dir, os.path.basename(full_img_url))
                    with open(img_name, 'wb') as img_file:
                        img_file.write(img_response.content)
                    #print(f"Saved web scraping image: {img_name}")
                except Exception as e:
                    print(f"Failed to save image from {full_img_url}: {e}")
            # else:
            #     print(f"Skipped non-image file: {full_img_url}")

        return clean_text, image_urls
    else:
        return None, None

from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi


def extract_video_id(youtube_url):
    parsed_url = urlparse(youtube_url)

    if 'youtube.com' in parsed_url.netloc:
        if 'v=' in parsed_url.query:
            return parse_qs(parsed_url.query)['v'][0]
        path_segments = parsed_url.path.split('/')
        return path_segments[path_segments.index('watch') + 1] if 'watch' in path_segments else None
    elif 'youtu.be' in parsed_url.netloc:
        return parsed_url.path[1:]
    else:
        return None

from transformers import AutoTokenizer, AutoModel
tokenizer = AutoTokenizer.from_pretrained("sentence-transformers/all-mpnet-base-v2")
model = AutoModel.from_pretrained("sentence-transformers/all-mpnet-base-v2")

def chunk_content_by_sentence(text):
    return sent_tokenize(text)

from langchain_ollama import OllamaLLM

llm = OllamaLLM(model="llama3.1:latest", temperature=0.5)

def generate_rag_response(query, model, tokenizer, index, content_chunks):
    query_inputs = tokenizer(query, return_tensors='pt')
    
    with torch.no_grad():
        query_embedding = model(**query_inputs).last_hidden_state.mean(dim=1).detach().numpy()
    
    k = 5  # Number of relevant contexts to retrieve
    distances, indices = index.search(query_embedding, k)
    
    relevant_contexts = [content_chunks[i] for i in indices[0]]
    combined_context = " ".join(relevant_contexts)

    # The input text for the Llama model
    input_text = (
        f"### Context Overview:\n"
        f"{combined_context}\n\n"
        f"### Instructions:\n"
        f"Using the provided context, please answer the following question. Your response should:\n"
        f"- Be as clear and concise as possible.\n"
        f"- Only answer the question based on the information in the context.\n"
        f"- If the answer is not present in the context, respond with 'I don't know' instead of providing an incorrect answer.\n"
        f"- Keep your answer no longer than three sentences.\n\n"
        f"### Question:\n"
        f"{query}\n\n"
        f"### Your Answer:"
    )

    # Call the Llama model via the custom `llm` function
    response = llm.invoke(input_text, temperature=0.3)
    
    # Return the generated response (assuming `llm` returns a string)
    return response.strip()


# def extract_frames_from_video(video_path, frame_rate=1):
#     Path('frames').mkdir(parents=True, exist_ok=True)  
#     frame_list = []
#     cap = cv2.VideoCapture("./rag_video.mp4")
#     frame_count = 0
#     while cap.isOpened():
#         ret, frame = cap.read()
#         if not ret:
#             break
#         if frame_count % (frame_rate * 30) == 0:  
#             frame_filename = f"frames/frame_{frame_count // 30}.jpg"  
#             cv2.imwrite(frame_filename, frame)
#             frame_list.append(frame_filename)
#         frame_count += 1
#     cap.release()
#     return frame_list


# def complete_video_func(client, image_path, model, previous_context, user_prompt):
#     base64_image = encode_to_64(image_path)
#     prompt = "Describe the image"
#     image_description = image_to_text(client, model, base64_image, prompt)
    
#     combined_context = previous_context + f"\nCurrent Frame Description: {image_description}"
    
#     return further_query(client, combined_context, user_prompt)

# def video_to_text(client, saved_video_path, model, user_prompt, frame_rate=1):
#     frames = extract_frames_from_video(saved_video_path, frame_rate)
#     all_responses = []
#     previous_context = ""  

#     for frame in frames:
#         result = complete_video_func(client, frame, model, previous_context, user_prompt)
#         all_responses.append(f"Frame Description: {result}")
        
#         # Update previous context with the result
#         previous_context += f"Frame Description: {result}\n"  

#     return "\n".join(all_responses)

# def final_video_context(all_results):
#     prompt = f"""
#     Given the following frame descriptions, analyze and extract important details such as actions, interactions, emotions, colors, background colors, and any contextual elements. 
#     Provide an overall summary that covers all frames instead of a frame-wise breakdown. This summary should capture the essence of the video, highlighting significant patterns, themes, or repeated actions. 
#     Avoid creating narratives or assumptions beyond what is explicitly described.

#     {all_results}
#     """
#     narrative = llm.invoke(prompt, temperature=0.2)
#     return narrative

def final_func(user_query, user_image, pdf_file, url, youtube_url, model, tokenizer, web_dir="extracted_images/webscraping"):
    if user_image:
        return complete_image_func(client, user_image, llava_model, user_query)
    # elif user_video:
    #     all_results = video_to_text(client, user_video, llava_model, user_query)
    #     return final_video_context(all_results)
    else:

        pdf_text = extract_text_and_images_from_pdf(pdf_file)
        web_text, scraped_images = scrape_page(url, web_dir)
        video_id = extract_video_id(youtube_url)
        transcript = YouTubeTranscriptApi.get_transcript(video_id) 
        yt_text = " ".join([entry['text'] for entry in transcript])

        text = web_text + pdf_text + yt_text

        content_chunks = chunk_content_by_sentence(text)

        chunk_embeddings = []
        for chunk in content_chunks:
            inputs = tokenizer(chunk, return_tensors='pt', max_length=512, truncation=True)
            with torch.no_grad():
                embedding = model(**inputs).last_hidden_state.mean(dim=1).numpy()
            chunk_embeddings.append(embedding)

        embeddings_np = np.vstack(chunk_embeddings)

        dimension = embeddings_np.shape[1]
        index = faiss.IndexFlatL2(dimension)
        index.add(embeddings_np)

        return generate_rag_response(user_query, model, tokenizer, index, content_chunks)


# print(final_func("What is going on in the video?", "images/image_page1_1.jpeg", "tanish-proposal-projX.pdf","football.mp4", "https://google.com", "https://youtu.be/_BiAhjJIWx8?si=w1_t6spLHqbnWf6G", model, tokenizer))

# final_res = final_func("What is Tanish?", "tanish-proposal-projX.pdf", "https://timesofindia.indiatimes.com/world/us/hurricane-milton-triggers-ef-3-tornado-in-floridas-palm-beach-county-leaving-seven-injured/articleshow/114176496.cms",
#            "https://youtu.be/_BiAhjJIWx8?si=w1_t6spLHqbnWf6G", model, tokenizer)
# print(final_res)
# -------------------------------Streamlit UI---------------------------------------

import streamlit as st
st.title("Not so Generic Chatbot")

uploaded_file = st.file_uploader("Upload your PDF", type="pdf")
uploaded_image = st.file_uploader("Upload an image", type=["png", "jpg", "jpeg"])
uploaded_video = st.file_uploader("Upload a video", type=["mp4", "mov", "avi", "mkv"])
if uploaded_file:
    saved_pdf_path = "./rag_document.pdf"  # Local directory and file name

    # Write the file's content to the local file system
    with open(saved_pdf_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

if uploaded_image:
    saved_image_path = "./rag_image.jpg"

    with open(saved_image_path, "wb") as f:
        f.write(uploaded_image.getbuffer())

if uploaded_video:
    saved_video_path = "./rag_video.mp4"

    with open(saved_video_path, "wb") as f:
        f.write(uploaded_video.getbuffer())

webpage_url = st.text_input("Upload a webpage URL")
youtube_url = st.text_input("Upload a Youtube video link")
st.write(f"Web URL: {webpage_url}")
st.write(f"YT URL: {youtube_url}")

if 'conversation' not in st.session_state:
    st.session_state.conversation = []

if uploaded_file is not None:
    st.success("Document uploaded successfully")
else:
    uploaded_file = "tanish-proposal-projX.pdf"

if webpage_url is "":
    webpage_url = "https://google.com"
if youtube_url is "":
    youtube_url = "https://youtu.be/_BiAhjJIWx8?si=w1_t6spLHqbnWf6G"

if st.session_state.conversation:
    for entry in st.session_state.conversation:
        st.write(f"**You:** {entry['question']}")
        st.write(f"**Bot:** {entry['answer']}")

user_query = st.chat_input("Ask a question")
with st.chat_message("user"):
    st.write("Hey there!")
    if user_query:
        response = final_func(user_query, uploaded_image, uploaded_file, webpage_url, youtube_url, model, tokenizer)
        # response = llm(user_query)
        st.session_state.conversation.append({'question': user_query, 'answer': response})
        st.write(f"**You:** {user_query}")
        st.write(f"**Bot:** {response}")
        # st.markdown(response)

