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
    functions_schema = """
    You are an expert in interpreting user queries for a Jenkins MCP server.
    Your primary goal is to translate the user's natural language query into a
    single, specific JSON object that represents a function call to the MCP server.
    This JSON object MUST have two keys: "action" (a string) and "parameters" (an object).

    Carefully analyze the user's query: "{query}"

    Here is a list of available Jenkins job names on the server: [{job_names_list}]
    Use this list to help resolve descriptive job names provided by the user to an actual job name.

    Follow these rules strictly:
    1. Identify ONE main intent from the query that maps to one of the available functions.
    2. Extract all necessary parameters for that chosen function. If a required parameter is missing
       and cannot be reasonably inferred, you may have to select a different function or make a
       best guess if appropriate (e.g., default "limit" for listing).
    3. Job Name Resolution:
       - The `job_name` parameter is critical. Job names can be simple (e.g., "testwinlaptop") or paths representing jobs in folders (e.g., "MyFolder/MySubFolder/MyJob").
       - The list of available Jenkins job names is: [{job_names_list}]. This list contains full job paths.
       - Users might provide:
         a) An exact job name/path (e.g., "MyFolder/MyJob").
         b) A descriptive phrase for a job (e.g., "the windows test machine").
         c) Multiple terms that could form parts of a job path (e.g., "imaging pipelines production").
       - Your goal is to map the user's input to the MOST SPECIFIC and ACCURATE job name from the provided list.
       - **Strategy for mapping user query to a `job_name` or `folder_name`:**

         **Step 1: Identify the Fullest Possible Target Path from the Query**
         Your primary goal here is to find the most specific (longest) path from the user's query that corresponds to an actual job or folder hierarchy present in [{job_names_list}].
         - **Process:**
           1. **Extract Key Naming Components:** From the user's query, identify all words or phrases that seem to represent parts of a job or folder name. Ignore generic words like "list", "builds", "status", "job", "folder", "subfolder" unless they are part of a formal name in [{job_names_list}].
              - Example Query: "list builds imaging pipelines, production subfolder, data_build job"
              - Key Naming Components: "imaging pipelines", "production", "data_build"
              - Example Query: "status of MyFolder job"
              - Key Naming Components: "MyFolder"
           2. **Normalize and Combine Components:**
              - Attempt to match and combine consecutive components from the query to known segments in [{job_names_list}]. For instance, "imaging pipelines" might map to a single segment "ImagingPipelines" if such a job/folder exists.
              - Be flexible with casing and minor variations if it helps find a match in [{job_names_list}].
           3. **Construct Candidate Paths:** Systematically try to form paths by joining the extracted and normalized components with slashes ('/').
              - Start with the first component.
              - Then try the first two components joined (e.g., "Component1/Component2").
              - Then the first three, and so on, up to all components.
              - Example (from "imaging pipelines", "production", "data_build"):
                - Candidate A: "ImagingPipelines" (assuming "imaging pipelines" maps to this)
                - Candidate B: "ImagingPipelines/production"
                - Candidate C: "ImagingPipelines/production/data_build"
           4. **Validate Against Job List:** For each candidate path constructed:
              - Check if it exactly matches a full job/folder name in [{job_names_list}].
              - Check if it is a prefix of any full job/folder name in [{job_names_list}].
           5. **Select Longest Valid Path:** The `identified_target_path` is the **longest** candidate path from step 3 that is either an exact match or a valid prefix found in step 4.
         - **Crucial Example for Path Identification (Illustrating component extraction and path construction):**
           - User Query: "list builds imaging pipelines, production subfolder, data_build job"
           - Assume [{job_names_list}] includes "ImagingPipelines/production/data_build", "ImagingPipelines/production/another_job", "ImagingPipelines/archive/jobB".
           - Step 1 (Extract Key Naming Components): "imaging pipelines", "production", "data_build".
           - Step 2 (Normalize/Combine - hypothetical): "ImagingPipelines", "production", "data_build".
           - Step 3 (Construct Candidate Paths):
             - "ImagingPipelines"
             - "ImagingPipelines/production"
             - "ImagingPipelines/production/data_build"
           - Step 4 (Validate):
             - "ImagingPipelines" is a prefix. Valid.
             - "ImagingPipelines/production" is a prefix. Valid.
             - "ImagingPipelines/production/data_build" is an exact match. Valid.
           - Step 5 (Select Longest): `identified_target_path` MUST BE "ImagingPipelines/production/data_build".

         - **Another Crucial Example (Simpler case):**
           - User Query: "list jobs in imaging pipelines production folder"
           - Assume [{job_names_list}] includes "ImagingPipelines/production/jobA", "ImagingPipelines/archive/jobB".
           - Step 1 (Extract Key Naming Components): "imaging pipelines", "production".
           - Step 2 (Normalize/Combine): "ImagingPipelines", "production".
           - Step 3 (Construct): "ImagingPipelines", "ImagingPipelines/production".
           - Step 4 (Validate): "ImagingPipelines" (prefix), "ImagingPipelines/production" (prefix).
           - Step 5 (Select Longest): `identified_target_path` MUST BE "ImagingPipelines/production".

         **Step 2: Determine if `identified_target_path` is a Job or a Folder**
            - The `identified_target_path` is a **FOLDER** if it appears as an exact prefix for other, longer job names in [{job_names_list}] (e.g., if `identified_target_path` is "MyFolder" and "MyFolder/MyJob" exists in the list).
            - The `identified_target_path` is a **JOB** if it exists in [{job_names_list}] and is NOT a prefix for any other longer job names in the list.
            - Example: If `identified_target_path` is "ImagingPipelines/production" (determined from Step 1):
                - It's a FOLDER if [{job_names_list}] contains "ImagingPipelines/production/jobX".
                - It's a JOB if [{job_names_list}] contains "ImagingPipelines/production" but no "ImagingPipelines/production/jobX" (or similar longer paths starting with this prefix).

         **Step 3: Action Selection based on Target Type and User Intent**
            - **If `identified_target_path` is a JOB:**
                - For actions like `get_build_status`, `list_job_builds`, `trigger_build`, `get_build_log`, use the `identified_target_path` as the `job_name`.
            - **If `identified_target_path` is a FOLDER:**
                - If the user's original intent was `list_jobs` (e.g., "list jobs in imaging pipelines production"), use the `identified_target_path` for the `folder_name` parameter of the `list_jobs` action. Set `recursive: false` by default unless specified.
                - If the user's original intent was build-related (e.g., `get_build_status`, `list_job_builds` for "imaging pipelines production") BUT the `identified_target_path` (e.g., "ImagingPipelines/production") is determined to be a FOLDER:
                    - **You MUST NOT use the folder path as `job_name` for these build-specific actions.**
                    - Instead, you MUST switch the action to `list_jobs` and use the `identified_target_path` as the `folder_name` parameter (e.g., `folder_name="ImagingPipelines/production"`). Set `recursive: true` to help the user discover specific jobs within that folder hierarchy, as their original query ("{query}") was about builds or specific job activities within this path.
            - **If Ambiguous or No Clear Path:** If you cannot confidently determine an `identified_target_path` or it's unclear if it's a job/folder, defaulting to `list_jobs` (potentially with a broader inferred `folder_name` or at the root) is a safe fallback.

       - **Illustrative Examples based on the above 3-step strategy:**
         1. Query: "list builds in the 'MyProject/ReleaseCandidates' folder"
            (Assume [{job_names_list}] contains "MyProject/ReleaseCandidates/JobA", "MyProject/ReleaseCandidates/Nightly/JobB", and "MyProject/ReleaseCandidates" is identified as a FOLDER)
            Output:
            `{{
              "action": "list_jobs",
              "parameters": {{
                "folder_name": "MyProject/ReleaseCandidates",
                "recursive": true
              }}
            }}`

         2. Query: "list builds imaging pipelines production"
            - Step 1: `identified_target_path` becomes "ImagingPipelines/production" (as per "Crucial Example").
            - Step 2: Assume "ImagingPipelines/production/jobA" exists in [{job_names_list}]. So, "ImagingPipelines/production" is a FOLDER.
            - Step 3: Original intent "list builds" is build-related. Target is a FOLDER.
            - Output MUST be: `{{ "action": "list_jobs", "parameters": {{ "folder_name": "ImagingPipelines/production", "recursive": true }} }}`

         3. Query: "status of ImagingPipelines/production/jobA build 5"
            - Step 1: `identified_target_path` is "ImagingPipelines/production/jobA".
            - Step 2: Assume "ImagingPipelines/production/jobA" is not a prefix for any other job. So, it's a JOB.
            - Step 3: Original intent "get_build_status". Target is a JOB.
            - Output: `{{ "action": "get_build_status", "parameters": {{ "job_name": "ImagingPipelines/production/jobA", "build_number": 5 }} }}`

         3. Query: "show jobs in ImagingPipelines"
            - Step 1: `identified_target_path` is "ImagingPipelines".
            - Step 2: Assume "ImagingPipelines/jobC" exists. So, "ImagingPipelines" is a FOLDER.
            - Step 3: Original intent "list_jobs". Target is a FOLDER.
            - Output: `{{ "action": "list_jobs", "parameters": {{ "folder_name": "ImagingPipelines", "recursive": false }} }}`

    4. If the query is complex, ambiguous, or seems to ask for multiple distinct actions,
       prioritize the most specific and actionable part according to the 3-step strategy above.
    5. Your output MUST be ONLY the JSON object. No preliminary text, no explanations, no apologies,
       just the JSON. Ensure the JSON is well-formed.

    Available functions and their schemas:
    1. get_build_status: Gets the status of a specific build.
       Action name: "get_build_status"
       Parameters:
         - job_name (string, required): The FULL name of the Jenkins job (e.g., "MyFolder/MyJob"). This MUST be an actual job, not a folder. If the user's query points to a folder for this action, you should use `list_jobs` for that folder instead.
         - build_number (string or integer, required): The build identifier (e.g., 123, "lastBuild").

    2. list_job_builds: Lists recent builds for a specific job.
       Action name: "list_job_builds"
       Parameters:
         - job_name (string, required): The FULL name of the Jenkins job (e.g., "MyFolder/MyJob"). This MUST be an actual job, not a folder. If the user's query points to a folder for this action, you should use `list_jobs` for that folder instead.
         - limit (integer, optional, default: 5): Number of recent builds to show.

    3. trigger_build: Triggers a new build for a specific job.
       Action name: "trigger_build"
       Parameters:
         - job_name (string, required): The name of the Jenkins job.
         - build_parameters (object, optional): A JSON object of parameters for the build (e.g., {{"GIT_BRANCH": "develop"}}).

    4. list_jobs: Lists available Jenkins jobs.
       Action name: "list_jobs"
       Parameters:
         - folder_name (string, optional): The base folder to search in.
         - recursive (boolean, optional, default: false): Whether to search recursively.

    5. get_build_log: Gets the console log for a specific build.
       Action name: "get_build_log"
       Parameters:
         - job_name (string, required): The name of the Jenkins job (e.g., "testwinlaptop", "MyFolder/MyJob").
         - build_number (string or integer, required): The build identifier.
           Can be a specific number (e.g., 123, "123").
           Supported keywords: "lastBuild", "lastSuccessfulBuild", "lastCompletedBuild".

    User query: "{query}"
    Available Jenkins jobs: [{job_names_list}]

    Based on the user query and the available job list, identify the most appropriate action and extract its parameters.
    Return a single JSON object with "action" and "parameters" keys.

    Example (assuming "testwinlaptop" and "main-ci-pipeline" are in [{job_names_list}]):

    Query: "what happened in testwinlaptop? what is the latest build?"
    Output:
    {{
      "action": "get_build_status",
      "parameters": {{
        "job_name": "testwinlaptop",
        "build_number": "lastBuild"
      }}
    }}

    Query: "what is the status of the test ms windows machine build?"
    Output:
    {{
      "action": "get_build_status",
      "parameters": {{
        "job_name": "testwinlaptop", 
        "build_number": "lastBuild"
      }}
    }}
    
    Query: "what happened with the main CI pipeline?"
    Output:
    {{
      "action": "list_job_builds",
      "parameters": {{
        "job_name": "main-ci-pipeline", 
        "limit": 3
      }}
    }}

    Query: "get log for MyJob build 7" (assuming "MyJob" is in [{job_names_list}])
    Output:
    {{
      "action": "get_build_log",
      "parameters": {{
        "job_name": "MyJob",
        "build_number": 7
      }}
    }}

    Query: "tell me about the build for the windows laptop, specifically the build just before the last one" (assuming "testwinlaptop" is the best match in [{job_names_list}])
    Output:
    {{
      "action": "list_job_builds",
      "parameters": {{
        "job_name": "testwinlaptop",
        "limit": 5 
      }}
    }}

    If the query is "build jobA with param1=value1 and param2=value2" (assuming "jobA" is in [{job_names_list}]):
    Output:
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

    If the query is just "what is the latest build for testwinlaptop?" (assuming "testwinlaptop" is in [{job_names_list}]):
    Output:
    {{
        "action": "get_build_status",
        "parameters": {{
            "job_name": "testwinlaptop",
            "build_number": "lastBuild"
        }}
    }}

    If a query is compound like "Show me jobs in 'folderA' and what was the last build of 'jobX'?" (assuming "jobX" is in [{job_names_list}]):
    Prioritize the most specific request or the one that seems most important. For example:
    Output:
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
