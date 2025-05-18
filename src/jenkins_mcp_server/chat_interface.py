import os
import requests
import sys
import re
import argparse

MCP_SERVER_URL = "http://localhost:5000"
MCP_API_KEY = os.environ.get('MCP_API_KEY')

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
        raw_ollama_url = os.environ.get('OLLAMA_URL')
        if not raw_ollama_url:
            raise ValueError("OLLAMA_URL not found in environment variables.")
        OLLAMA_URL = raw_ollama_url.replace("/v1", "")
        OLLAMA_MODEL_FAST = model
        # OLLAMA_MODEL_FAST comes from args, so only OLLAMA_URL needs to be checked from env here for this specific error
        if not OLLAMA_URL: # This check might be redundant now but kept for safety
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

        if not job_name:
            raise ValueError("job_name not found in Ollama response")
        if build_number is None:
            build_number = "lastBuild" # Default to latest build if not specified

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        return f"Error parsing response: {e}. Response: {ollama_text}"

    # Call the MCP server
    try:
        headers = {}
        if MCP_API_KEY:
            headers['X-API-Key'] = MCP_API_KEY
        else:
            # Optionally, raise an error or log if API key is expected but not found client-side
            print("Warning: MCP_API_KEY not found in client environment. Request might be unauthorized.", file=sys.stderr)

        # Corrected URL structure to match the server endpoint
        mcp_response = requests.get(f"{MCP_SERVER_URL}/job/{job_name}/build/{build_number}", headers=headers)
        mcp_response.raise_for_status()
        response_json = mcp_response.json()
        
        # Determine status based on 'building' and 'result' fields
        is_building = response_json.get('building')
        build_result = response_json.get('result')

        if is_building:
            result = "BUILDING"
        elif build_result:
            result = build_result
        else:
            # This case might occur if the build is finished but result is None (should be rare)
            # or if the build is in a very early stage not yet marked as 'building' but has no 'result'.
            result = "UNKNOWN" 
            
    except requests.exceptions.RequestException as e:
        result = f"Error calling MCP server: {e}"
    except KeyError as e:
        result = f"Error parsing MCP server response - missing key: {e}. Response: {mcp_response.text}"
    except Exception as e: # Catch any other unexpected errors during parsing
        result = f"An unexpected error occurred while processing MCP server response: {e}. Response: {mcp_response.text}"

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
