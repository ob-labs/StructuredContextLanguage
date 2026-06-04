## Steps for set up this example

```
uv sync
export EMBEDDING_LOCAL_MODEL_PATH=path_to_your_embedding_weight/bge-m3
or
export EMBEDDING_BASE_URL=http://0.0.0.0:9080
export EMBEDDING_API_KEY="any"
python example/BFCL/gothroughfunctions.py
```

to run embedding service via restful way you can run code below in your own device.

```
# server.py
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Union
from sentence_transformers import SentenceTransformer
import uvicorn

# 1. 加载模型（可换成你自己的路径或 HuggingFace 模型名）
model = SentenceTransformer("./bge-m3")

app = FastAPI(title="Local Embedding Service (OpenAI format)")

class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]]
    model: str = "local-model"

class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: List[dict]
    model: str
    usage: dict

@app.post("/embeddings", response_model=EmbeddingResponse)
async def get_embeddings(req: EmbeddingRequest):
    # 统一转为列表
    texts = [req.input] if isinstance(req.input, str) else req.input
    
    # 2. 编码
    embeddings = model.encode(texts, normalize_embeddings=True)  # shape: (n, dim)
    
    # 3. 计算 token 数（用模型自带 tokenizer）
    tokenizer = model.tokenizer
    encoded = tokenizer(texts, padding=True, truncation=True, return_tensors="pt")
    token_counts = encoded["attention_mask"].sum(dim=1).tolist()
    
    # 4. 构建 OpenAI 格式响应
    data = []
    for i, (emb, tc) in enumerate(zip(embeddings, token_counts)):
        data.append({
            "object": "embedding",
            "embedding": emb.tolist(),
            "index": i
        })
    
    total_tokens = sum(token_counts)
    return EmbeddingResponse(
        data=data,
        model=req.model,
        usage={
            "prompt_tokens": total_tokens,
            "total_tokens": total_tokens
        }
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9080)
```
