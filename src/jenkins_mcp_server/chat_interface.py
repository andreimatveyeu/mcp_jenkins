import os
import requests
import sys
import re
import argparse

MCP_SERVER_URL = "http://localhost:5000"

#if not OLLAMA_URL or not OLLAMA_MODEL_FAST:

def chat(query, model):
    # Use Google Gemini if specified
    if model == "gemini-2.0-flash-001":
        import google.generativeai as genai
        GOOGLE_AISTUDIO_API_KEY = os.environ.get("GOOGLE_AISTUDIO_API_KEY")
        if not GOOGLE_AISTUDIO_API_KEY:
            raise ValueError("GOOGLE_AISTUDIO_API_KEY not found in environment variables.")

        genai.configure(api_key=GOOGLE_AISTUDIO_API_KEY)
        gemini_model = genai.GenerativeModel(model)
        ollama_prompt = f"You are an expert in extracting job_name and build_number from user queries for a Jenkins MCP server. The MCP server is located at {MCP_SERVER_URL}. Given the user query: '{query}', return a JSON object containing ONLY 'job_name' and 'build_number'. Do not include any other text or explanations."
        response = gemini_model.generate_content(ollama_prompt)
        ollama_text = response.text.strip()
        ollama_text = re.sub(r"<think>.*?</think>", "", ollama_text)

    # Use Ollama if not Gemini
    else:
        OLLAMA_URL = os.environ.get('OLLAMA_URL').replace("/v1", "")
        OLLAMA_MODEL_FAST = model
        if not OLLAMA_URL or not OLLAMA_MODEL_FAST:
            raise ValueError("Ollama credentials not found in environment variables.")

        ollama_prompt = f"You are an expert in extracting job_name and build_number from user queries for a Jenkins MCP server. The MCP server is located at {MCP_SERVER_URL}. Given the user query: '{query}', return a JSON object containing ONLY 'job_name' and 'build_number'. Do not include any other text or explanations."
        ollama_data = {
            "prompt": ollama_prompt,
            "model": OLLAMA_MODEL_FAST,
            "stream": False
        }
        ollama_response = requests.post(f"{OLLAMA_URL}/api/generate", json=ollama_data)
        ollama_response.raise_for_status()
        ollama_text = ollama_response.json()['response'].strip()
        ollama_text = re.sub(r"<think>.*?</think>", "", ollama_text)

    # Extract JSON from markdown block if it exists
    match = re.search(r"```json\s*([\s\S]*?)\s*```", ollama_text)
    if match:
        ollama_text = match.group(1)

    # Extract job_name and build_number from Ollama response
    try:
        import json
        ollama_json = json.loads(ollama_text)
        job_name = ollama_json.get('job_name')
        build_number = ollama_json.get('build_number')

        if not job_name or not build_number:
            raise ValueError("job_name or build_number not found in Ollama response")

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        return f"Error parsing response: {e}. Response: {ollama_text}"

    # Extract JSON from markdown block if it exists
    match = re.search(r"```json\s*([\s\S]*?)\s*```", ollama_text)
    if match:
        ollama_text = match.group(1)

    # Extract job_name and build_number from response
    try:
        import json
        ollama_json = json.loads(ollama_text)
        job_name = ollama_json.get('job_name')
        build_number = ollama_json.get('build_number')

        if not job_name or not build_number:
            raise ValueError("job_name or build_number not found in response")

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        return f"Error parsing JSON: {e}. Response: {ollama_text}"

    # Call the MCP server
    try:
        mcp_response = requests.get(f"{MCP_SERVER_URL}/build_status?job_name={job_name}&build_number={build_number}")
        mcp_response.raise_for_status()
        result = mcp_response.json()['status']
    except requests.exceptions.RequestException as e:
        result = f"Error calling MCP server: {e}"

    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Chat with Jenkins MCP server")
    parser.add_argument("query", help="The query to send to the MCP server")
    parser.add_argument("--model", default="deepseek-r1:14b", help="The model to use (e.g., deepseek-r1:14b, gemini-2.0-flash-001)")
    args = parser.parse_args()

    try:
        result = chat(args.query, args.model)
        print(f"Query: {args.query}")
        print(f"Result: {result}")
    except Exception as e:
        print(f"Error: {e}")
