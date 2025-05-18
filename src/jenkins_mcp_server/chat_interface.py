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
    # Fetch available Jenkins jobs to help the LLM with job name resolution
    available_jobs_response = call_mcp_server("/jobs?recursive=true") # Fetch all jobs
    job_names_list = []
    if isinstance(available_jobs_response, dict) and "jobs" in available_jobs_response:
        job_names_list = [job.get("name") for job in available_jobs_response["jobs"] if job.get("name")]
    
    job_names_for_prompt = ", ".join(f'"{name}"' for name in job_names_list) if job_names_list else "No jobs found or error fetching jobs."

    # Define available functions/intents for the LLM
    script_dir = os.path.dirname(__file__)
    schema_file_path = os.path.join(script_dir, "functions_schema.md")
    with open(schema_file_path, "r") as inp:
        functions_schema = inp.read()
    prompt = functions_schema.format(query=query, job_names_list=job_names_for_prompt)

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

def _prompt_for_missing_details_create_job(params):
    """Interactively prompts user for missing details for create_job action."""
    if not params.get("job_name"):
        params["job_name"] = input("Enter the desired job name: ").strip()
        if not params["job_name"]:
            return "Job name cannot be empty."

    if not params.get("job_type"):
        while True:
            job_type_input = input("Enter job type (calendar/weather): ").strip().lower()
            if job_type_input in ["calendar", "weather"]:
                params["job_type"] = job_type_input
                break
            else:
                print("Invalid job type. Please enter 'calendar' or 'weather'.")

    if params["job_type"] == "calendar":
        if not params.get("month"):
            while True:
                try:
                    month_input = int(input("Enter month (1-12) for calendar job: ").strip())
                    if 1 <= month_input <= 12:
                        params["month"] = month_input
                        break
                    else:
                        print("Month must be between 1 and 12.")
                except ValueError:
                    print("Invalid month. Please enter a number.")
        if not params.get("year"):
            while True:
                try:
                    year_input = int(input("Enter year (e.g., 2024) for calendar job: ").strip())
                    # Basic year validation, can be more robust
                    if 1900 <= year_input <= 2100:
                        params["year"] = year_input
                        break
                    else:
                        print("Year seems invalid. Please enter a valid year (e.g., 1900-2100).")
                except ValueError:
                    print("Invalid year. Please enter a number.")
    elif params["job_type"] == "weather":
        if not params.get("city"):
            params["city"] = input("Enter city for weather job: ").strip()
            if not params["city"]:
                return "City name cannot be empty for weather job."
    
    if "job_description" not in params: # Optional, but good to ask if not provided by LLM
        desc_input = input("Enter an optional job description (or press Enter to skip): ").strip()
        if desc_input:
            params["job_description"] = desc_input
            
    return params


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

    elif action == "get_build_log":
        job_name = params.get("job_name")
        build_number = params.get("build_number")
        if not job_name or build_number is None: # build_number can be 0 or "lastBuild"
            return "Error: Missing job_name or build_number for get_build_log."
        
        # The server endpoint for getting a build log:
        # GET /job/<path:job_path>/build/<build_number_str>/log
        result = call_mcp_server(f"/job/{job_name}/build/{build_number}/log")
        
        # The server now returns a summary and a log URL.
        if isinstance(result, dict) and "summary" in result and "log_url" in result:
            return (f"Summary for {result.get('job_name', job_name)} build {result.get('build_number', build_number)}:\n"
                    f"{result['summary']}\n"
                    f"Full log available at: {result['log_url']}")
        elif isinstance(result, dict) and "error" in result: # Pass through server error messages
             return f"Error from server: {result['error']}"
        return result # Return other error strings or unexpected structures from call_mcp_server

    elif action == "create_job":
        # Prompt for missing details if necessary
        updated_params = _prompt_for_missing_details_create_job(params.copy()) # Pass a copy
        if isinstance(updated_params, str): # Error message returned
            return updated_params

        # Server expects payload like:
        # { "job_name": "name", "job_type": "calendar", "month": 1, "year": 2023, "city": null, "job_description": "desc" }
        payload_for_server = {
            "job_name": updated_params.get("job_name"),
            "job_type": updated_params.get("job_type"),
            "month": updated_params.get("month"),
            "year": updated_params.get("year"),
            "city": updated_params.get("city"),
            "job_description": updated_params.get("job_description")
        }
        
        # Filter out None values as server Pydantic models handle Optional fields
        payload_for_server = {k: v for k, v in payload_for_server.items() if v is not None}

        result = call_mcp_server("/job/create", method="POST", data=payload_for_server)
        if isinstance(result, dict) and "message" in result:
            return f"{result['message']}. Job: {result.get('job_name')}, URL: {result.get('job_url')}"
        elif isinstance(result, dict) and "error" in result:
             return f"Error from server: {result['error']}"
        return result


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
