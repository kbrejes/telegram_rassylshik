import numpy as np
from openai import OpenAI

class LLMClient:
    def __init__(self, base_url, model, embedding_model=None):
        self.client = OpenAI(
            base_url=base_url,
            api_key="lm-studio"
        )
        self.model = model
        self.embedding_model = embedding_model or model

    def chat(self, messages, temperature=0.7):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature
        )
        return response.choices[0].message.content
    
    def embed(self, text: str) -> np.ndarray:
        resp = self.client.embeddings.create(
            model=self.embedding_model,
            input=text
        )
        return np.array(resp.data[0].embedding, dtype="float32")