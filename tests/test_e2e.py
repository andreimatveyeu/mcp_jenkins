import pytest
import requests
import time
import subprocess
import os
import json

# Imports for the client
from mcp_jenkins.client import get_llm_instruction, call_mcp_server
import google.generativeai as genai # For direct LLM call for verification

# Configuration for the local MCP server process (adapted from tests/test_server.py)
# The client.py uses MCP_SERVER_URL = "http://localhost:5000" by default.
# The server_process fixture uses SERVER_PORT = os.getenv("SERVER_PORT", "8002").
# We will use monkeypatch to make the client use the fixture's server URL.
SERVER_PORT = os.getenv("SERVER_PORT", "8002")
FIXTURE_SERVER_URL = f"http://localhost:{SERVER_PORT}"

# The server application is server.py, located at /app/src/mcp_jenkins/server.py inside the container
# (as per Dockerfile WORKDIR /app and COPY commands in the project's Docker setup)
SERVER_COMMAND = ["python", "/app/src/mcp_jenkins/server.py"]
STARTUP_TIMEOUT = 15  # seconds to wait for server to start

# API Key for MCP Server communication - client.py will pick this up from os.environ.get('MCP_API_KEY')
# The server started by SERVER_COMMAND will also pick up MCP_API_KEY from its environment.
# Ensure MCP_API_KEY is set consistently in the test environment.

@pytest.fixture(scope="session") # Changed to session scope as it's an e2e test setup
def server_process():
    # Ensure GOOGLE_AISTUDIO_API_KEY is set for Gemini models,
    # and MCP_API_KEY for server auth if enabled.
    # These are environment prerequisites for the tests to run.
    if not os.getenv("GOOGLE_AISTUDIO_API_KEY"):
        pytest.skip("GOOGLE_AISTUDIO_API_KEY not set, skipping e2e test involving Gemini.")
    
    # Optional: Check for MCP_API_KEY if your server strictly requires it.
    # if not os.getenv("MCP_API_KEY"):
    #     pytest.skip("MCP_API_KEY not set, skipping e2e test if server requires auth.")

    print(f"Starting server with command: {' '.join(SERVER_COMMAND)} for e2e tests.")
    print(f"Server will be expected at: {FIXTURE_SERVER_URL}")
    
    # Pass relevant environment variables to the subprocess
    env = os.environ.copy()
    env["SERVER_PORT"] = SERVER_PORT # Ensure the server uses the correct port

    process = subprocess.Popen(SERVER_COMMAND, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    start_time = time.time()
    server_ready = False
    while time.time() - start_time < STARTUP_TIMEOUT:
        try:
            response = requests.get(f"{FIXTURE_SERVER_URL}/health", timeout=1)
            if response.status_code == 200:
                print("Server started successfully for e2e tests.")
                server_ready = True
                break
        except requests.ConnectionError:
            time.sleep(0.5)
        except requests.Timeout:
            print("Server health check timed out during e2e setup, retrying...")
            time.sleep(0.5)

    if not server_ready:
        stdout, stderr = process.communicate()
        print(f"Server failed to start within {STARTUP_TIMEOUT} seconds for e2e tests.")
        print(f"STDOUT: {stdout.decode(errors='ignore')}")
        print(f"STDERR: {stderr.decode(errors='ignore')}")
        process.terminate()
        process.wait()
        pytest.fail(f"MCP Server did not start on {FIXTURE_SERVER_URL} within {STARTUP_TIMEOUT}s for e2e tests.")

    yield process # The server process object

    print("Terminating server process after e2e tests...")
    process.terminate()
    try:
        process.wait(timeout=10) # Increased timeout for graceful shutdown
    except subprocess.TimeoutExpired:
        print("Server process did not terminate gracefully, killing.")
        process.kill()
        process.wait() # Ensure kill is processed
    print("Server process terminated after e2e tests.")


def test_list_jobs_e2e(server_process, monkeypatch):
    """
    End-to-end test:
    1. Starts the MCP server.
    2. Uses the MCP client to send a "list jobs" query via a Gemini model.
    3. Verifies the response from the client/LLM does not indicate an error.
    """
    assert server_process is not None, "Server process fixture failed to run for e2e test."

    # Patch the client's MCP_SERVER_URL to point to the server started by the fixture.
    # client.py defines MCP_SERVER_URL at the module level.
    monkeypatch.setattr("mcp_jenkins.client.MCP_SERVER_URL", FIXTURE_SERVER_URL)
    
    # Also patch the MCP_API_KEY used by the client, if it's different from what test_server.py might use
    # However, client.py already uses os.environ.get('MCP_API_KEY'), which should be the source of truth.
    # If test_server.py's MCP_API_KEY_FOR_TESTS is different and needs to be aligned,
    # ensure the environment variable MCP_API_KEY is set to what the server expects.

    query = "list jobs"
    model = "gemini-2.0-flash-001"  # As specified in the task

    print(f"E2E Test: Sending query '{query}' with model '{model}' via client.")
    
    # This call involves:
    # - Client fetching /jobs from our MCP server (FIXTURE_SERVER_URL).
    # - Client calling Google AI Studio API with the prompt and model.
    # - Client parsing the LLM's JSON response.
    instruction = get_llm_instruction(query, model)
    
    print(f"E2E Test: LLM Instruction received: {json.dumps(instruction, indent=2)}")

    # Assertion: The response shall contain no error.
    # An error from get_llm_instruction is typically a dict with an "error" key.
    assert "error" not in instruction, \
        f"LLM instruction returned an error: {instruction.get('error')}"
    
    # Further assertions for a valid instruction structure
    assert "action" in instruction, \
        "LLM instruction is missing the 'action' key."
    assert isinstance(instruction["action"], str), \
        f"'action' key should be a string, got {type(instruction['action'])}."
    
    assert "parameters" in instruction, \
        "LLM instruction is missing the 'parameters' key."
    assert isinstance(instruction["parameters"], dict), \
        f"'parameters' key should be a dict, got {type(instruction['parameters'])}."

    # For a "list jobs" query, the expected action is "list_jobs"
    assert instruction["action"] == "list_jobs", \
        f"Expected action 'list_jobs', but got '{instruction['action']}'."

    print("E2E Test: Initial LLM instruction for 'list jobs' obtained successfully.")

    # Step 2: Execute the "list_jobs" action by calling the MCP server
    print("E2E Test: Executing 'list_jobs' action by calling MCP server...")
    mcp_action_params = instruction.get("parameters", {})
    recursive = mcp_action_params.get("recursive", False) # Default to False if not specified
    folder_name = mcp_action_params.get("folder_name")

    jobs_endpoint = "/jobs"
    query_params_list = []
    if folder_name:
        query_params_list.append(f"folder_name={requests.utils.quote(folder_name)}")
    if recursive:
        query_params_list.append("recursive=true")
    
    if query_params_list:
        jobs_endpoint += "?" + "&".join(query_params_list)

    # Ensure MCP_SERVER_URL is patched for call_mcp_server as well
    # This is already done by monkeypatch.setattr("mcp_jenkins.client.MCP_SERVER_URL", FIXTURE_SERVER_URL)
    # call_mcp_server uses the MCP_SERVER_URL defined in mcp_jenkins.client
    
    # The MCP_API_KEY is picked up by call_mcp_server from os.environ.
    # Ensure it's set in the test environment if the server requires it.
    # The server started by SERVER_COMMAND in this test file will also pick it up from its env.

    jobs_response = call_mcp_server(jobs_endpoint, method="GET")
    print(f"E2E Test: MCP Server response for {jobs_endpoint}: {json.dumps(jobs_response, indent=2)}")

    assert isinstance(jobs_response, dict), \
        f"Expected dict response from MCP server for {jobs_endpoint}, got {type(jobs_response)}. Response: {jobs_response}"
    assert "error" not in jobs_response, \
        f"MCP server returned an error for {jobs_endpoint}: {jobs_response.get('error')}"
    assert "jobs" in jobs_response, \
        f"MCP server response for {jobs_endpoint} missing 'jobs' key. Response: {jobs_response}"
    assert isinstance(jobs_response["jobs"], list), \
        f"'jobs' key in MCP server response for {jobs_endpoint} is not a list. Got {type(jobs_response['jobs'])}. Response: {jobs_response}"

    print(f"E2E Test: Successfully fetched jobs from MCP server. Found {len(jobs_response['jobs'])} items.")

    # Step 3: Send the job list to LLM for verification
    jobs_data_for_llm = jobs_response.get("jobs", [])
    jobs_data_str = json.dumps(jobs_data_for_llm)

    # Use the same model as the initial query for verification for consistency
    verification_model_name = model 

    verification_prompt = (
        "Please analyze the following JSON data. Does this appear to be a valid list of Jenkins job objects? "
        "Are there any obvious errors or inconsistencies? "
        "Respond with a JSON object containing a 'valid_jenkins_jobs' (boolean) key, "
        "and an optional 'findings' (string) key if issues are found or for general observations. "
        "If the list is empty but valid (e.g. an empty array), it should be considered valid.\n\n"
        f"Data:\n{jobs_data_str}"
    )
    
    print(f"E2E Test: Sending job data to LLM ({verification_model_name}) for verification.")

    # Re-initialize genai if necessary, or ensure it's configured from the fixture
    # GOOGLE_AISTUDIO_API_KEY is checked in the fixture.
    # genai.configure is called in get_llm_instruction, but good to be explicit if needed,
    # however, get_llm_instruction already configures it globally for the google.generativeai module.
    
    gemini_model_for_verification = genai.GenerativeModel(verification_model_name)
    generation_config = genai.types.GenerationConfig(
        response_mime_type="application/json" # Crucial for getting JSON output
    )
    
    try:
        llm_verification_response = gemini_model_for_verification.generate_content(
            verification_prompt,
            generation_config=generation_config
        )
        verification_text = llm_verification_response.text.strip()
        print(f"E2E Test: LLM verification response text: {verification_text}")
        parsed_verification = json.loads(verification_text)
    except Exception as e:
        pytest.fail(f"E2E Test: Error during LLM verification step: {e}. LLM response was: '{getattr(llm_verification_response, 'text', 'N/A')}'")

    print(f"E2E Test: LLM Verification (parsed JSON): {json.dumps(parsed_verification, indent=2)}")

    # Step 4: Assert verification result
    assert "valid_jenkins_jobs" in parsed_verification, \
        f"LLM verification JSON response missing 'valid_jenkins_jobs' key. Response: {parsed_verification}"
    assert isinstance(parsed_verification["valid_jenkins_jobs"], bool), \
        f"'valid_jenkins_jobs' key should be a boolean, got {type(parsed_verification['valid_jenkins_jobs'])}. Response: {parsed_verification}"
    
    assert parsed_verification["valid_jenkins_jobs"] is True, \
        (f"LLM verification indicated the job list is not valid or has errors. "
         f"Findings: {parsed_verification.get('findings', 'N/A')}. Original jobs data: {jobs_data_str}")

    print(f"E2E Test: LLM verification successful. Findings: {parsed_verification.get('findings', 'None')}")
    print("E2E Test: 'list jobs' full flow completed and verified successfully.")

@pytest.fixture(scope="function") # Run for each test that uses it
def cleanup_all_jobs_e2e(server_process, monkeypatch):
    """
    Fixture to clean up all Jenkins jobs and folders by calling MCP server endpoints directly.
    This runs BEFORE the test that uses it.
    """
    assert server_process is not None, "Server process fixture failed to run for cleanup."
    
    # Ensure client's MCP_SERVER_URL is patched to the test server
    monkeypatch.setattr("mcp_jenkins.client.MCP_SERVER_URL", FIXTURE_SERVER_URL)
    
    print("\nE2E Cleanup: Attempting to clean up all Jenkins jobs and folders...")

    # 1. Get all jobs and folders (recursively)
    all_items_response = call_mcp_server("/jobs?recursive=true", method="GET")
    
    if not isinstance(all_items_response, dict) or "jobs" not in all_items_response:
        print(f"E2E Cleanup: Could not retrieve items for cleanup. Response: {all_items_response}")
        # Optionally, could fail here if cleanup is critical: pytest.fail("Failed to list items for cleanup")
        yield # Proceed with the test, though cleanup might be incomplete
        return

    items_to_delete = all_items_response.get("jobs", [])
    if not items_to_delete:
        print("E2E Cleanup: No items found to delete.")
        yield
        return

    # Sort items by length of name descending to try to delete nested items first.
    # Jenkins' folder deletion is often recursive, but this adds a layer of safety.
    items_to_delete.sort(key=lambda x: len(x.get("name", "")), reverse=True)

    deleted_count = 0
    failed_to_delete = []

    for item in items_to_delete:
        item_name = item.get("name")
        if not item_name:
            continue

        # The delete endpoint is /job/{full_path}/delete for both jobs and folders
        delete_url_path = f"/job/{requests.utils.quote(item_name)}/delete" # Ensure item_name is URL-encoded
        print(f"E2E Cleanup: Attempting to delete '{item_name}' via MCP server at {delete_url_path}")
        
        delete_response = call_mcp_server(delete_url_path, method="POST") 

        if isinstance(delete_response, dict) and delete_response.get("message", "").startswith("Successfully deleted"):
            print(f"E2E Cleanup: Successfully deleted '{item_name}'.")
            deleted_count += 1
        elif isinstance(delete_response, dict) and "error" in delete_response and "404" in delete_response.get("error", ""):
            print(f"E2E Cleanup: Item '{item_name}' not found (404), assuming already deleted.")
        elif isinstance(delete_response, str) and "404" in delete_response: # call_mcp_server might return string on HTTPError
            print(f"E2E Cleanup: Item '{item_name}' not found (404 string response), assuming already deleted.")
        else:
            error_msg = f"E2E Cleanup: Failed to delete '{item_name}'. Response: {delete_response}"
            print(error_msg)
            failed_to_delete.append(item_name)
        
        time.sleep(0.2) # Small delay

    print(f"E2E Cleanup: Finished. Deleted {deleted_count} items. Failed to delete: {failed_to_delete if failed_to_delete else 'None'}.")
    
    if failed_to_delete:
        print(f"E2E Cleanup WARNING: Not all items were cleaned up: {failed_to_delete}")

    yield # Test runs here

    print("E2E Cleanup: Post-test cleanup phase completed.")


def test_create_job_e2e(server_process, monkeypatch, cleanup_all_jobs_e2e):
    """
    End-to-end test for creating a job and verifying its existence:
    1. Cleans up existing jobs (via fixture).
    2. Uses the MCP client (LLM) to get instructions for creating a job.
    3. Executes the job creation via MCP server.
    4. Uses the MCP client (LLM) to get instructions for listing jobs.
    5. Executes listing jobs via MCP server.
    6. Verifies with LLM that the created job is in the list.
    """
    assert server_process is not None, "Server process fixture failed to run for e2e test."
    # cleanup_all_jobs_e2e fixture has already run and patched MCP_SERVER_URL

    model_to_use = "gemini-2.0-flash-001" 
    created_job_name = "mycooljob"
    created_job_command = "echo 1233456"
    created_job_description = "My cool job"

    # 1. Get LLM instruction to create the job
    create_query = f"create job {created_job_name} running {created_job_command} command, description '{created_job_description}'"
    print(f"\nE2E Create Test: Sending create query '{create_query}' with model '{model_to_use}'.")
    
    create_instruction = get_llm_instruction(create_query, model_to_use)
    print(f"E2E Create Test: LLM Create Instruction: {json.dumps(create_instruction, indent=2)}")

    assert "error" not in create_instruction, \
        f"LLM create instruction returned an error: {create_instruction.get('error')}"
    assert create_instruction.get("action") == "create_job", \
        f"Expected action 'create_job', got '{create_instruction.get('action')}'."
    
    create_params = create_instruction.get("parameters", {})
    assert create_params.get("job_name") == created_job_name, \
        f"LLM did not extract job_name correctly. Expected '{created_job_name}', got '{create_params.get('job_name')}'."
    assert created_job_command in create_params.get("command", ""), \
        f"LLM did not extract command correctly. Expected '{created_job_command}' in '{create_params.get('command', '')}'."
    assert create_params.get("job_description") == created_job_description, \
        f"LLM did not extract description correctly. Expected '{created_job_description}', got '{create_params.get('job_description')}'."

    print("E2E Create Test: LLM instruction for job creation is valid.")

    # 2. Execute job creation via MCP server
    job_creation_payload = {
        "job_name": create_params.get("job_name"),
        "command": create_params.get("command"),
        "job_description": create_params.get("job_description"),
        "folder_name": create_params.get("folder_name") 
    }
    job_creation_payload = {k: v for k, v in job_creation_payload.items() if v is not None}

    print(f"E2E Create Test: Calling MCP server to create job with payload: {json.dumps(job_creation_payload)}")
    creation_response = call_mcp_server("/job/create", method="POST", data=job_creation_payload)
    print(f"E2E Create Test: MCP Server Create Job Response: {json.dumps(creation_response, indent=2)}")

    assert isinstance(creation_response, dict), \
        f"Expected dict response from MCP server for job creation, got {type(creation_response)}. Response: {creation_response}"
    assert "error" not in creation_response, \
        f"MCP server returned an error during job creation: {creation_response.get('error')}"
    assert creation_response.get("message", "") == "Job created successfully", \
        f"Job creation failed or message unexpected. Response: {creation_response}"
    assert creation_response.get("job_name") == created_job_name, \
        f"Created job name in response mismatch. Expected '{created_job_name}', got '{creation_response.get('job_name')}'."

    print(f"E2E Create Test: Job '{created_job_name}' created successfully via MCP server.")
    time.sleep(2) 

    # 3. List jobs and verify the new job is present using LLM
    list_query = "list jobs recursive" 
    print(f"E2E Create Test: Sending list query '{list_query}' to verify job creation.")
    
    list_instruction = get_llm_instruction(list_query, model_to_use)
    print(f"E2E Create Test: LLM List Instruction: {json.dumps(list_instruction, indent=2)}")
    assert "error" not in list_instruction, f"LLM list instruction returned an error: {list_instruction.get('error')}"
    assert list_instruction.get("action") == "list_jobs", f"Expected 'list_jobs' action, got '{list_instruction.get('action')}'."

    list_params = list_instruction.get("parameters", {})
    list_recursive = list_params.get("recursive", True) # Default to true for verification
    list_folder = list_params.get("folder_name")
    
    list_jobs_endpoint = "/jobs"
    list_query_params_list = []
    if list_folder:
        list_query_params_list.append(f"folder_name={requests.utils.quote(list_folder)}")
    if list_recursive: 
        list_query_params_list.append("recursive=true")
    if list_query_params_list:
        list_jobs_endpoint += "?" + "&".join(list_query_params_list)
    
    print(f"E2E Create Test: Calling MCP server to list jobs via {list_jobs_endpoint}.")
    listed_jobs_response = call_mcp_server(list_jobs_endpoint, method="GET")
    print(f"E2E Create Test: MCP Server List Jobs Response: {json.dumps(listed_jobs_response, indent=2)}")

    assert isinstance(listed_jobs_response, dict) and "jobs" in listed_jobs_response, \
        f"Invalid response when listing jobs for verification. Response: {listed_jobs_response}"
    
    jobs_data_for_verification = listed_jobs_response.get("jobs", [])
    jobs_data_str_for_verification = json.dumps(jobs_data_for_verification)

    # 4. LLM Verification of the job list
    verification_prompt_for_creation = (
        f"Please analyze the following JSON data, which is a list of Jenkins job objects. "
        f"Is the job named '{created_job_name}' present in this list? "
        "Also, does the list itself appear valid and without obvious errors? "
        "Respond with a JSON object containing: "
        "  'job_present' (boolean, true if '{created_job_name}' is found), "
        "  'list_valid' (boolean, true if the list structure seems correct), "
        "  and an optional 'findings' (string) key for any observations.\n\n"
        f"Data:\n{jobs_data_str_for_verification}"
    )

    print(f"E2E Create Test: Sending job list to LLM ({model_to_use}) for verification of '{created_job_name}'.")
    
    gemini_model_for_verification = genai.GenerativeModel(model_to_use)
    generation_config = genai.types.GenerationConfig(response_mime_type="application/json")
    
    try:
        llm_final_verification_response = gemini_model_for_verification.generate_content(
            verification_prompt_for_creation,
            generation_config=generation_config
        )
        final_verification_text = llm_final_verification_response.text.strip()
        parsed_final_verification = json.loads(final_verification_text)
    except Exception as e:
        pytest.fail(f"E2E Create Test: Error during final LLM verification: {e}. LLM response: '{getattr(llm_final_verification_response, 'text', 'N/A')}'")

    print(f"E2E Create Test: LLM Final Verification (parsed): {json.dumps(parsed_final_verification, indent=2)}")

    assert "job_present" in parsed_final_verification and isinstance(parsed_final_verification["job_present"], bool), \
        "LLM final verification missing 'job_present' boolean key."
    assert "list_valid" in parsed_final_verification and isinstance(parsed_final_verification["list_valid"], bool), \
        "LLM final verification missing 'list_valid' boolean key."

    assert parsed_final_verification["list_valid"] is True, \
        f"LLM indicated the job list is not valid. Findings: {parsed_final_verification.get('findings', 'N/A')}"
    assert parsed_final_verification["job_present"] is True, \
        f"LLM did not find the created job '{created_job_name}' in the list. Findings: {parsed_final_verification.get('findings', 'N/A')}"

    print(f"E2E Create Test: LLM successfully verified presence of '{created_job_name}' and list validity.")
    print("E2E Create Test: 'create job' full flow completed and verified successfully.")
