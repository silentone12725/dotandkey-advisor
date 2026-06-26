#!/usr/bin/env python3
import os
import asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI
import httpx

load_dotenv(override=True)

async def test_openai_compatible(name, api_key, base_url, model):
    if not api_key or not base_url or 'xxxx' in api_key:
        print(f"[{name}] Skipping (No valid API key or base URL configured)")
        return
    
    print(f"\n[{name}] Pinging {base_url} ...")
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        
        # Test models list
        try:
            models = await client.models.list()
            model_ids = [m.id for m in models.data]
            print(f"  -> Available Models: {', '.join(model_ids[:5])}" + ("..." if len(model_ids) > 5 else ""))
        except Exception as e:
            print(f"  -> Could not list models: {e}")

        # Test completion
        resp = await client.chat.completions.create(
            model=model,
            messages=[{'role':'user', 'content':'Say hi in one word.'}],
            max_tokens=10,
        )
        print(f"  -> SUCCESS! Model '{model}' responded: {resp.choices[0].message.content.strip()}")
    except Exception as e:
        print(f"  -> FAILED: {e}")

async def test_ollama(name, base_url, model):
    if not base_url:
        print(f"[{name}] Skipping (No base URL configured)")
        return
        
    print(f"\n[{name}] Pinging {base_url} ...")
    try:
        client = AsyncOpenAI(api_key="ollama", base_url=base_url)
        
        try:
            models = await client.models.list()
            model_ids = [m.id for m in models.data]
            print(f"  -> Available Models: {', '.join(model_ids[:5])}" + ("..." if len(model_ids) > 5 else ""))
        except Exception as e:
            print(f"  -> Could not list models: {e}")

        resp = await client.chat.completions.create(
            model=model,
            messages=[{'role':'user', 'content':'Say hi in one word.'}],
            max_tokens=10,
        )
        print(f"  -> SUCCESS! Model '{model}' responded: {resp.choices[0].message.content.strip()}")
    except Exception as e:
        print(f"  -> FAILED: {e}")

async def test_gemini(name, api_key, model):
    if not api_key or 'xxxx' in api_key:
        print(f"[{name}] Skipping (No valid API key configured)")
        return
        
    print(f"\n[{name}] Pinging Gemini API ...")
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            model_ids = [m['name'].split('/')[-1] for m in data.get('models', [])]
            print(f"  -> Available Models: {', '.join(model_ids[:5])}" + ("..." if len(model_ids) > 5 else ""))
            
            gen_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            resp = await client.post(gen_url, json={
                "contents": [{"parts": [{"text": "Say hi in one word."}]}]
            })
            resp.raise_for_status()
            gen_data = resp.json()
            text = gen_data['candidates'][0]['content']['parts'][0]['text']
            print(f"  -> SUCCESS! Model '{model}' responded: {text.strip()}")
    except Exception as e:
        print(f"  -> FAILED: {e}")

async def main():
    print("=== Testing API Reachability & Models ===")
    await test_openai_compatible(
        "NIM", 
        os.getenv('NIM_API_KEY'), 
        os.getenv('NIM_BASE_URL'), 
        os.getenv('NIM_MODEL', 'qwen/qwen3.5-122b-a10b')
    )
    
    await test_ollama(
        "Ollama", 
        os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'), 
        os.getenv('OLLAMA_MODEL', 'qwen3.5:4b')
    )
    
    await test_openai_compatible(
        "OpenAI", 
        os.getenv('OPENAI_API_KEY'), 
        os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'), 
        os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
    )
    
    await test_gemini(
        "Google Gemini", 
        os.getenv('GOOGLE_API_KEY'), 
        os.getenv('GOOGLE_MODEL', 'gemini-2.0-flash')
    )
    print("\n=== Done ===")

if __name__ == "__main__":
    asyncio.run(main())
