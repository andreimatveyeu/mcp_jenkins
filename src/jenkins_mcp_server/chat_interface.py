import os
import requests
import sys
import re
import argparse
import json # Ensure json is imported

MCP_SERVER_URL = "http://localhost:5000"
MCP_API_KEY = os.environ.get('MCP_API_KEY')

def call_mcp_server(endpoint, method="GET", data=None):
    headers = {}
    if MCP_API_KEY:
        headers['X-API-Key'] = MCP_API_KEY
    else:
        print("Warning: MCP_API_KEY not found. Request might be unauthorized.", file=sys.stderr)

    url = f"{MCP_SERVER_URL}{endpoint}"
    try:
        if method.upper() == "GET":
            mcp_response = requests.get(url, headers=headers)
        elif method.upper() == "POST":
            headers['Content-Type'] = 'application/json'
            mcp_response = requests.post(url, headers=headers, json=data)
        else:
            return f"Unsupported HTTP method: {method}"

        mcp_response.raise_for_status()
        return mcp_response.json()
    except requests.exceptions.HTTPError as e:
        # Try to get more detailed error from server response if possible
        error_details = ""
        try:
            error_payload = e.response.json()
            if "error" in error_payload:
                error_details = f" Server message: {error_payload['error']}"
        except ValueError: # If response is not JSON
            error_details = f" Server response: {e.response.text}"
        return f"Error calling MCP server ({e.response.status_code} {e.response.reason}) for {url}.{error_details}"
    except requests.exceptions.RequestException as e:
        return f"Error calling MCP server: {e}"
    except json.JSONDecodeError:
        return f"Error parsing MCP server JSON response. Response: {mcp_response.text}"


def get_llm_instruction(query, model):
    # Define available functions/intents for the LLM
    functions_schema = """
    You are an expert in interpreting user queries for a Jenkins MCP server.
    Your primary goal is to translate the user's natural language query into a
    single, specific JSON object that represents a function call to the MCP server.
    This JSON object MUST have two keys: "action" (a string) and "parameters" (an object).

    Carefully analyze the user's query: "{query}"

    Follow these rules strictly:
    1. Identify ONE main intent from the query that maps to one of the available functions.
    2. Extract all necessary parameters for that chosen function. If a required parameter is missing
       and cannot be reasonably inferred, you may have to select a different function or make a
       best guess if appropriate (e.g., default "limit" for listing).
    3. If the query is complex, ambiguous, or seems to ask for multiple distinct actions,
       prioritize the most specific and actionable part. If still unclear, choosing an action
       that provides broader context (like `list_job_builds` or `list_jobs`) can be a good strategy.
       For example, if the user asks about a build "just before the latest", `list_job_builds` is often
       a better choice than `get_build_status` if a specific build number cannot be determined.
    4. Your output MUST be ONLY the JSON object. No preliminary text, no explanations, no apologies,
       just the JSON. Ensure the JSON is well-formed.

    Available functions and their schemas:
    1. get_build_status: Gets the status of a specific build.
       Action name: "get_build_status"
       Parameters:
         - job_name (string, required): The name of the Jenkins job (e.g., "testwinlaptop", "MyFolder/MyJob").
         - build_number (string or integer, required): The build identifier.
           Can be a specific number (e.g., 123, "123").
           Supported keywords: "lastBuild", "lastSuccessfulBuild", "lastCompletedBuild".
           If the user says "latest build", use "lastBuild".
           If the user asks for a build relative to the latest (e.g., "the build before latest", "second to last build")
           and you cannot determine a specific build number directly from the query for this function,
           consider if `list_job_builds` would be more helpful.

    2. list_job_builds: Lists recent builds for a job. Useful for general inquiries like "what happened in job X?"
       or when the user asks for builds relative to the latest (e.g., "the build before latest")
       and a specific build number isn't easily determined for `get_build_status`.
       Action name: "list_job_builds"
       Parameters:
         - job_name (string, required): The name of the Jenkins job.
         - limit (integer, optional, default: 5): Number of recent builds to show.

    3. trigger_build: Triggers a new build for a job.
       Action name: "trigger_build"
       Parameters:
         - job_name (string, required): The name of the Jenkins job.
         - build_parameters (object, optional): A JSON object of parameters for the build (e.g., {{"GIT_BRANCH": "develop"}}).

    4. list_jobs: Lists available Jenkins jobs.
       Action name: "list_jobs"
       Parameters:
         - folder_name (string, optional): The base folder to search in.
         - recursive (boolean, optional, default: false): Whether to search recursively.

    User query: "{query}"

    Based on the user query, identify the most appropriate action and extract its parameters.
    Return a single JSON object with "action" and "parameters" keys.

    Example for "what happened in testwinlaptop? what is the latest build?":
    {{
      "action": "get_build_status",
      "parameters": {{
        "job_name": "testwinlaptop",
        "build_number": "lastBuild"
      }}
    }}

    Example for "what happened in testwinlaptop?":
    {{
      "action": "list_job_builds",
      "parameters": {{
        "job_name": "testwinlaptop",
        "limit": 3
      }}
    }}

    Example for "tell me about testwinlaptop, specifically the build just before the last one":
    {{
      "action": "list_job_builds",
      "parameters": {{
        "job_name": "testwinlaptop",
        "limit": 5
      }}
    }}

    If the query is "build jobA with param1=value1 and param2=value2":
    {{
      "action": "trigger_build",
      "parameters": {{
        "job_name": "jobA",
        "build_parameters": {{
          "param1": "value1",
          "param2": "value2"
        }}
      }}
    }}

    If the query is just "what is the latest build for testwinlaptop?":
    {{
        "action": "get_build_status",
        "parameters": {{
            "job_name": "testwinlaptop",
            "build_number": "lastBuild"
        }}
    }}

    If a query is compound like "Show me jobs in 'folderA' and what was the last build of 'jobX'?":
    Prioritize the most specific request or the one that seems most important. For example:
    {{
      "action": "get_build_status",
      "parameters": {{
        "job_name": "jobX",
        "build_number": "lastBuild"
      }}
    }}
    (This assumes getting the build status for jobX is the primary intent here).

    Output ONLY the JSON object. Do not include any other text or explanations.
    """
    prompt = functions_schema.format(query=query) # This line should now work

    if model == "gemini-2.0-flash-001":
        import google.generativeai as genai
        GOOGLE_AISTUDIO_API_KEY = os.environ.get("GOOGLE_AISTUDIO_API_KEY")
        if not GOOGLE_AISTUDIO_API_KEY:
            raise ValueError("GOOGLE_AISTUDIO_API_KEY not found.")
        genai.configure(api_key=GOOGLE_AISTUDIO_API_KEY)
        gemini_model = genai.GenerativeModel(model)
        generation_config = genai.types.GenerationConfig(
            response_mime_type="application/json"
        )
        response = gemini_model.generate_content(prompt, generation_config=generation_config)
        llm_text = response.text.strip()
    else: # Ollama
        raw_ollama_url = os.environ.get('OLLAMA_URL')
        if not raw_ollama_url:
            raise ValueError("OLLAMA_URL not found.")
        OLLAMA_URL = raw_ollama_url.replace("/v1", "")
        OLLAMA_MODEL_FAST = model
        ollama_data = {
            "prompt": prompt,
            "model": OLLAMA_MODEL_FAST,
            "format": "json",
            "stream": False
        }
        ollama_response = requests.post(f"{OLLAMA_URL}/api/generate", json=ollama_data)
        ollama_response.raise_for_status()
        llm_text = ollama_response.json()['response'].strip()

    try:
        parsed_instruction = json.loads(llm_text)
        if not isinstance(parsed_instruction, dict) or "action" not in parsed_instruction or "parameters" not in parsed_instruction:
            raise ValueError("LLM output is not in the expected format (missing action/parameters).")
        return parsed_instruction
    except json.JSONDecodeError as e:
        return {"error": f"Error parsing LLM JSON response: {e}. Response was: {llm_text}"}
    except ValueError as e:
        return {"error": f"{str(e)} LLM response was: {llm_text}"}

def execute_instruction(instruction):
    if "error" in instruction:
        return f"Error from LLM: {instruction['error']}"

    action = instruction.get("action")
    params = instruction.get("parameters", {})

    if action == "get_build_status":
        job_name = params.get("job_name")
        build_number = params.get("build_number")
        if not job_name or build_number is None: # build_number can be 0
            return "Error: Missing job_name or build_number for get_build_status."
        # The server endpoint for specific build status:
        # GET /job/<path:job_path>/build/<build_number_str>
        result = call_mcp_server(f"/job/{job_name}/build/{build_number}")
        if isinstance(result, dict): # Successful call
            is_building = result.get('building')
            build_result = result.get('result')
            status = "UNKNOWN"
            if is_building: status = "BUILDING"
            elif build_result: status = build_result
            return f"Status for {job_name} build {result.get('build_number', build_number)}: {status}. Details: {result.get('url')}"
        return result # Return error string from call_mcp_server

    elif action == "list_job_builds":
        job_name = params.get("job_name")
        limit = params.get("limit", 3) # Default to showing info for 3 builds
        if not job_name:
            return "Error: Missing job_name for list_job_builds."
        # The server endpoint for listing builds:
        # GET /job/<path:job_path>/builds
        builds_data = call_mcp_server(f"/job/{job_name}/builds")
        if isinstance(builds_data, dict) and "builds" in builds_data:
            builds = builds_data["builds"]
            if not builds:
                return f"No builds found for job {job_name}."
            summary = f"Recent builds for {job_name}:\n"
            for build in builds[:limit]:
                status = "BUILDING" if build.get('building') else build.get('result', 'UNKNOWN')
                summary += f"  - Build #{build['number']}: {status} ({build['url']})\n"
            return summary.strip()
        return builds_data # Return error string or unexpected structure

    elif action == "trigger_build":
        job_name = params.get("job_name")
        build_params = params.get("build_parameters") # This should be a dict for the server
        if not job_name:
            return "Error: Missing job_name for trigger_build."
        # The server endpoint for triggering a build:
        # POST /job/<path:job_path>/build
        # Server expects parameters directly in the JSON body, or within a "parameters" key if you adjust the Pydantic model
        # Current server BuildJobPayload expects: {"parameters": {"key": "value"}} OR if empty, it takes {}
        # Let's assume the LLM gives build_parameters as the flat dict. The server code tries to handle both cases.
        # For simplicity with current server:
        payload_for_server = {"parameters": build_params if build_params else {}}

        result = call_mcp_server(f"/job/{job_name}/build", method="POST", data=payload_for_server)
        if isinstance(result, dict) and "message" in result:
            return f"{result['message']} for {job_name}. Queue item: {result.get('queue_item')}"
        return result

    elif action == "list_jobs":
        folder_name = params.get("folder_name")
        recursive = params.get("recursive", False)
        endpoint = "/jobs"
        query_params = []
        if folder_name:
            query_params.append(f"folder_name={folder_name}")
        if recursive: # only add if true
            query_params.append(f"recursive=true")
        if query_params:
            endpoint += "?" + "&".join(query_params)

        jobs_data = call_mcp_server(endpoint)
        if isinstance(jobs_data, dict) and "jobs" in jobs_data:
            jobs = jobs_data["jobs"]
            if not jobs:
                return "No jobs found."
            summary = "Available jobs:\n"
            for job in jobs[:20]: # Limit output for brevity
                summary += f"  - {job['name']} ({job.get('_class', 'N/A')})\n"
            if len(jobs) > 20:
                summary += f"... and {len(jobs)-20} more."
            return summary.strip()
        return jobs_data
    else:
        return f"Unknown action: {action}"

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Chat with Jenkins MCP server")
    parser.add_argument("query", help="The query to send to the MCP server")
    parser.add_argument("--model", default="deepseek-coder:6.7b-instruct", help="The model to use (e.g., deepseek-coder:6.7b-instruct, gemini-2.0-flash-001). For Ollama, use a model that supports JSON mode well.")
    # Note: For gemini-2.0-flash-001, ensure GOOGLE_AISTUDIO_API_KEY is set.
    # For Ollama models, ensure OLLAMA_URL is set.
    args = parser.parse_args()

    print(f"Query: {args.query}")
    try:
        instruction = get_llm_instruction(args.query, args.model)
        # print(f"LLM Instruction:\n{json.dumps(instruction, indent=2)}") # Debug LLM output
        result = execute_instruction(instruction)
        print(f"Result:\n{result}")
    except Exception as e:
        print(f"Critical Client Error: {e}")
        import traceback
        traceback.print_exc()
