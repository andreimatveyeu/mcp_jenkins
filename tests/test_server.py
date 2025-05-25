import pytest
import requests
import time
import subprocess
import os

# Configuration for the local MCP server process
SERVER_PORT = os.getenv("SERVER_PORT", "8002")
SERVER_URL = f"http://localhost:{SERVER_PORT}"
# The server application is server.py, located at /app/src/mcp_jenkins/server.py inside the container
# (as per Dockerfile WORKDIR /app and COPY commands)
SERVER_COMMAND = ["python", "/app/src/mcp_jenkins/server.py"]
STARTUP_TIMEOUT = 15  # seconds to wait for server to start (increased slightly for Flask startup)

# API Key for MCP Server communication
MCP_API_KEY_FOR_TESTS = os.getenv("MCP_API_KEY")

AUTH_REQUEST_HEADERS = {}
if MCP_API_KEY_FOR_TESTS:
    AUTH_REQUEST_HEADERS["X-API-Key"] = MCP_API_KEY_FOR_TESTS

AUTH_POST_HEADERS_JSON = {"Content-Type": "application/json"}
if MCP_API_KEY_FOR_TESTS:
    AUTH_POST_HEADERS_JSON["X-API-Key"] = MCP_API_KEY_FOR_TESTS


assert "6211" in os.environ.get("JENKINS_URL", "")  ## safety check to run only on testing jenkins instances

@pytest.fixture(scope="module")
def server_process():
    print(f"Starting server with command: {' '.join(SERVER_COMMAND)}")
    process = subprocess.Popen(SERVER_COMMAND, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Wait for the server to start
    start_time = time.time()
    server_ready = False
    while time.time() - start_time < STARTUP_TIMEOUT:
        try:
            # Assuming a /health endpoint on the MCP server
            response = requests.get(f"{SERVER_URL}/health", timeout=1)
            if response.status_code == 200:
                print("Server started successfully.")
                server_ready = True
                break
        except requests.ConnectionError:
            time.sleep(0.5) # Wait and retry
        except requests.Timeout:
            print("Server health check timed out, retrying...")
            time.sleep(0.5)

    if not server_ready:
        stdout, stderr = process.communicate()
        print(f"Server failed to start within {STARTUP_TIMEOUT} seconds.")
        print(f"STDOUT: {stdout.decode()}")
        print(f"STDERR: {stderr.decode()}")
        process.terminate()
        process.wait()
        pytest.fail(f"MCP Server did not start on {SERVER_URL} within {STARTUP_TIMEOUT}s")
        return None # Should not reach here due to pytest.fail

    yield process

    print("Terminating server process...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print("Server process did not terminate gracefully, killing.")
        process.kill()
    print("Server process terminated.")

def test_server_is_running(server_process):
    """Test that the local MCP server starts and is accessible."""
    assert server_process is not None, "Server process fixture failed to run."
    try:
        # Test a basic endpoint of the local MCP server
        response = requests.get(SERVER_URL + "/")
        assert response.status_code == 200, f"Expected status code 200 for local server, got {response.status_code}"
        print(f"Successfully connected to local server at {SERVER_URL}/")
    except requests.ConnectionError as e:
        pytest.fail(f"Failed to connect to the local server at {SERVER_URL}. Error: {e}")
    except requests.Timeout:
        pytest.fail(f"Request to the local server at {SERVER_URL} timed out.")

def test_environment_setup():
    """Test that the necessary environment files and directories exist and are not empty."""
    # Check if test_jenkins_data directory is not empty
    test_jenkins_data_path = "test_jenkins_data"
    assert os.path.isdir(test_jenkins_data_path), f"The path '{test_jenkins_data_path}' is not a directory."
    assert len(os.listdir(test_jenkins_data_path)) > 0, f"The directory '{test_jenkins_data_path}' is empty."
    print(f"Directory '{test_jenkins_data_path}' exists and is not empty.")


@pytest.fixture(scope="function")
def jenkins_job_structure(request, server_process):
    """
    Setup fixture to create a specific Jenkins job/folder structure via API calls for testing.
    Teardown fixture to remove the created structure via API calls.
    """
    assert server_process is not None, "Server process fixture failed to run."

    # Define the structure to create via API calls
    # (name, type, parent_folder)
    structure_elements = [
        ("jobA", "job", None),
        ("folderB", "folder", None),
        ("jobB1", "job", "folderB"),
        ("folderB1", "folder", "folderB"),
        ("folderB2", "folder", "folderB/folderB1"),
        ("jobB2", "job", "folderB/folderB1/folderB2"),
    ]

    created_elements = [] # To keep track for teardown

    # --- Pre-cleanup step ---
    print("\nAttempting pre-cleanup of Jenkins job and folder structure...")
    # Define top-level items that might exist from previous runs
    # These are the items that would be created at the root by this fixture
    pre_cleanup_items = ["jobA", "folderB"] 
    for item_name_to_delete in pre_cleanup_items:
        delete_url = f"{SERVER_URL}/job/{item_name_to_delete}/delete" # Folders are deleted via /job/<name>/delete
        print(f"Pre-cleanup: Attempting to delete '{item_name_to_delete}' via MCP server at {delete_url}")
        try:
            # Using a shorter timeout for cleanup, as failure here is not critical for the test itself
            delete_response = requests.post(delete_url, headers=AUTH_POST_HEADERS_JSON, timeout=10)
            if delete_response.status_code == 200:
                print(f"Pre-cleanup: Successfully deleted '{item_name_to_delete}'.")
            elif delete_response.status_code == 404:
                print(f"Pre-cleanup: '{item_name_to_delete}' not found, no need to delete.")
            else:
                # Log other statuses but don't fail the fixture setup
                print(f"Pre-cleanup: Warning - Deleting '{item_name_to_delete}' returned status {delete_response.status_code}. Response: {delete_response.text}")
        except requests.RequestException as e:
            print(f"Pre-cleanup: Warning - Request error deleting '{item_name_to_delete}': {e}")
        except Exception as e:
            print(f"Pre-cleanup: Warning - Unexpected error deleting '{item_name_to_delete}': {e}")
        time.sleep(1) # Small delay after each potential delete

    print("\nCreating Jenkins job and folder structure via API for test...")

    for name, item_type, parent_folder in structure_elements:
        full_item_path = f"{parent_folder}/{name}" if parent_folder else name
        print(f"Attempting to create {item_type}: {full_item_path}")

        payload = {}
        if item_type == "folder":
            create_url = f"{SERVER_URL}/folder/create"
            payload["folder_name"] = full_item_path # Pass the full path of the folder to create
        elif item_type == "job":
            create_url = f"{SERVER_URL}/job/create"
            payload["job_name"] = name # Simple name for the job
            if parent_folder:
                payload["folder_name"] = parent_folder # Full path of the parent folder
            # Add job-specific details
            payload["job_type"] = "calendar"
            payload["month"] = 1
            payload["year"] = 2025
        else:
            # Should not happen based on structure_elements
            pytest.fail(f"Unknown item_type: {item_type}")

        max_retries = 10
        retry_delay = 2
        success = False

        for i in range(max_retries):
            try:
                create_response = requests.post(create_url, headers=AUTH_POST_HEADERS_JSON, json=payload, timeout=15)
                create_response.raise_for_status()
                assert create_response.status_code == 201, f"Failed to create {item_type} '{full_item_path}'. Status: {create_response.status_code}. Response: {create_response.text}"
                print(f"Successfully created {item_type}: {full_item_path}")
                created_elements.append(full_item_path)
                success = True
                break
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 409: # Conflict - already exists
                    print(f"{item_type} '{full_item_path}' already exists (status 409). Assuming it exists and proceeding.")
                    created_elements.append(full_item_path) # Add to list for cleanup
                    success = True
                    break
                else:
                    print(f"HTTP error creating {item_type} '{full_item_path}' (Attempt {i+1}/{max_retries}): {e}. Retrying...")
            except requests.RequestException as e:
                print(f"Request error creating {item_type} '{full_item_path}' (Attempt {i+1}/{max_retries}): {e}. Retrying...")
            except Exception as e:
                print(f"An unexpected error occurred creating {item_type} '{full_item_path}' (Attempt {i+1}/{max_retries}): {e}. Retrying...")
            
            time.sleep(retry_delay)
        
        assert success, f"Failed to create {item_type} '{full_item_path}' after {max_retries} attempts."
        time.sleep(5) # Give Jenkins time to process each creation

    # Teardown function
    def teardown():
        print("\nCleaning up Jenkins job and folder structure via API after test...")
        # Delete in reverse order of creation, starting with top-level folders/jobs
        # This ensures nested items are deleted when their parent folder is deleted
        # Or, more simply, just delete the top-level items, and Jenkins will handle recursion for folders.
        
        # The order of deletion matters for nested structures.
        # Delete jobA and folderB (which should recursively delete its contents)
        elements_to_delete = ["folderB", "jobA"] # Delete folderB first to clean up its contents

        for full_name in elements_to_delete:
            delete_url = f"{SERVER_URL}/job/{full_name}/delete"
            print(f"Attempting to delete '{full_name}' via MCP server at {delete_url}")
            max_retries = 10
            retry_delay = 2
            deleted_successfully = False

            for i in range(max_retries):
                try:
                    delete_response = requests.post(delete_url, headers=AUTH_POST_HEADERS_JSON, timeout=15)
                    delete_response.raise_for_status()
                    assert delete_response.status_code == 200, f"Failed to delete '{full_name}'. Status: {delete_response.status_code}. Response: {delete_response.text}"
                    print(f"Successfully deleted '{full_name}'.")
                    deleted_successfully = True
                    break
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 404:
                        print(f"'{full_name}' not found for deletion (status 404). Already removed or never created. Proceeding.")
                        deleted_successfully = True
                        break
                    else:
                        print(f"HTTP error deleting '{full_name}' (Attempt {i+1}/{max_retries}): {e}. Retrying...")
                except requests.RequestException as e:
                    print(f"Request error deleting '{full_name}' (Attempt {i+1}/{max_retries}): {e}. Retrying...")
                except Exception as e:
                    print(f"An unexpected error occurred deleting '{full_name}' (Attempt {i+1}/{max_retries}): {e}. Retrying...")
                
                time.sleep(retry_delay)
            
            if not deleted_successfully:
                print(f"Warning: Failed to delete '{full_name}' after {max_retries} attempts during cleanup.")
            time.sleep(2) # Give Jenkins time to process each deletion

    request.addfinalizer(teardown)
    yield

def test_list_jobs_recursive(server_process, jenkins_job_structure):
    """Test recursive listing of jobs via the local MCP server."""
    assert server_process is not None, "Server process fixture failed to run."

    base_url = SERVER_URL # Use local server URL

    # Non-recursive call
    try:
        response_non_recursive = requests.get(f"{base_url}/jobs", timeout=10, headers=AUTH_REQUEST_HEADERS)
        response_non_recursive.raise_for_status() # Raise an exception for HTTP error codes
    except requests.RequestException as e:
        pytest.fail(f"Failed to get non-recursive job list from local server: {e}")

    assert response_non_recursive.status_code == 200, \
        f"Expected 200 OK for non-recursive /jobs (local), got {response_non_recursive.status_code}. Response: {response_non_recursive.text}"

    jobs_data_non_recursive = response_non_recursive.json().get("jobs")
    assert isinstance(jobs_data_non_recursive, list), "Expected 'jobs' to be a list in non-recursive response (local)"

    actual_jobs_non_recursive = [j for j in jobs_data_non_recursive if j.get("type") != "folder" and "_class" in j]
    count_actual_jobs_non_recursive = len(actual_jobs_non_recursive)
    print(f"Non-recursive actual jobs found (local, {count_actual_jobs_non_recursive}):")
    for job in actual_jobs_non_recursive:
        print(f"  - {job.get('name')}")

    # Recursive call
    try:
        response_recursive = requests.get(f"{base_url}/jobs?recursive=true", timeout=20, headers=AUTH_REQUEST_HEADERS) # Longer timeout
        response_recursive.raise_for_status()
    except requests.RequestException as e:
        pytest.fail(f"Failed to get recursive job list from local server: {e}")

    assert response_recursive.status_code == 200, \
        f"Expected 200 OK for recursive /jobs (local), got {response_recursive.status_code}. Response: {response_recursive.text}"

    jobs_data_recursive = response_recursive.json().get("jobs")
    assert isinstance(jobs_data_recursive, list), "Expected 'jobs' to be a list in recursive response (local)"

    actual_jobs_recursive = [j for j in jobs_data_recursive if j.get("type") != "folder" and "_class" in j]
    count_actual_jobs_recursive = len(actual_jobs_recursive)
    print(f"Recursive actual jobs found (local, {count_actual_jobs_recursive}):")
    for job in actual_jobs_recursive:
        print(f"  - {job.get('name')}")

    assert count_actual_jobs_recursive >= count_actual_jobs_non_recursive, \
        (f"Recursive actual job count (local, {count_actual_jobs_recursive}) "
             f"should be >= non-recursive actual job count (local, {count_actual_jobs_non_recursive})")

    if count_actual_jobs_recursive > count_actual_jobs_non_recursive:
        non_recursive_job_names = {job['name'] for job in actual_jobs_non_recursive}
        recursive_job_names = {job['name'] for job in actual_jobs_recursive}

        newly_found_job_names = recursive_job_names - non_recursive_job_names

        assert newly_found_job_names, \
                ("Expected to find new job names in recursive call when its count is higher (local), "
                 "but the set difference is empty.")

        found_nested_in_newly_found = False
        for name in newly_found_job_names:
            if "/" in name:
                found_nested_in_newly_found = True
                print(f"Found newly listed nested job (local): {name}")
                break

        assert found_nested_in_newly_found, \
                (f"When recursive call ({count_actual_jobs_recursive} jobs) finds more actual jobs than non-recursive "
                 f"({count_actual_jobs_non_recursive} jobs) (local), at least one of the *newly found* jobs "
                 f"must be a nested job (name containing '/'). Newly found jobs: {newly_found_job_names}. ")

    elif count_actual_jobs_recursive > 0 and count_actual_jobs_recursive == count_actual_jobs_non_recursive:
        found_any_nested_job = False
        for job in actual_jobs_recursive:
            if "/" in job.get("name", ""):
                found_any_nested_job = True
                break
        assert found_any_nested_job, \
                (f"Recursive and non-recursive calls found the same number of actual jobs ({count_actual_jobs_recursive}) (local), "
                 f"but no jobs with '/' in their names were identified. ")

    print("Recursive job listing test completed.")

def test_create_and_delete_job(server_process):
    """Test creating, verifying, and deleting a Jenkins job via the MCP server."""
    assert server_process is not None, "Server process fixture failed to run."

    job_name = f"test-job-{int(time.time())}" # Unique job name
    # Job will be created at the root level

    # Payload for the MCP server's /job/create endpoint
    create_payload = {
        "job_name": job_name,
        "job_type": "calendar", # Using calendar type as an example
        # No folder_name in payload for root creation
        "month": 1,
        "year": 2025,
        "job_description": "Test job created by functional test at root"
    }

    try:
        # Create the job via MCP server
        create_url = f"{SERVER_URL}/job/create"
        print(f"Attempting to create job '{job_name}' via MCP server at {create_url}")
        create_response = requests.post(create_url, headers=AUTH_POST_HEADERS_JSON, json=create_payload, timeout=10)
        create_response.raise_for_status()
        assert create_response.status_code == 201, f"Failed to create job via MCP server. Status code: {create_response.status_code}. Response: {create_response.text}"
        print(f"Job '{job_name}' created successfully via MCP server.")

        # Verify the job exists via MCP server's list jobs endpoint
        # We might need to wait a moment for Jenkins to register the job
        time.sleep(2) # Give Jenkins a moment

        verify_exists = False
        # Check in the recursive job list from MCP server
        list_jobs_url = f"{SERVER_URL}/jobs?recursive=true" # Use recursive to find it anywhere just in case
        print(f"Verifying job '{job_name}' existence via MCP server at {list_jobs_url}")
        list_response = requests.get(list_jobs_url, timeout=10, headers=AUTH_REQUEST_HEADERS)
        list_response.raise_for_status()
        assert list_response.status_code == 200, f"Failed to list jobs via MCP server for verification. Status code: {list_response.status_code}. Response: {list_response.text}"

        jobs_data = list_response.json().get("jobs", [])
        for job in jobs_data:
            if job.get("name") == job_name: # Check for root level name
                verify_exists = True
                print(f"Job '{job_name}' verified to exist via MCP server listing.")
                break

        assert verify_exists, f"Job '{job_name}' not found in MCP server job listing after creation."

    except requests.RequestException as e:
        pytest.fail(f"Test failed during job creation or verification via MCP server: {e}")
    except Exception as e:
        pytest.fail(f"An unexpected error occurred during test execution: {e}")

    finally:
        # Clean up: Delete the job via MCP server
        delete_url = f"{SERVER_URL}/job/{job_name}/delete" # Use job_name for root deletion
        print(f"Attempting to delete job '{job_name}' via MCP server at {delete_url}")
        try:
            delete_response = requests.post(delete_url, headers=AUTH_POST_HEADERS_JSON, timeout=10)
            delete_response.raise_for_status()
            assert delete_response.status_code == 200, f"Failed to delete job '{job_name}' via MCP server during cleanup. Status code: {delete_response.status_code}. Response: {delete_response.text}"
            print(f"Job '{job_name}' deleted successfully via MCP server.")
        except requests.RequestException as e:
            print(f"Warning: Failed to delete job '{job_name}' via MCP server during cleanup: {e}")
        except Exception as e:
            print(f"Warning: An unexpected error occurred during job deletion cleanup via MCP server: {e}")

def test_create_and_delete_folder(server_process):
    """Test creating, verifying, and deleting a Jenkins folder via the MCP server."""
    assert server_process is not None, "Server process fixture failed to run."

    folder_name = f"test-folder-{int(time.time())}" # Unique folder name

    # Payload for the MCP server's /folder/create endpoint
    create_payload = {
        "folder_name": folder_name
    }

    try: # Outer try
        # Clean up any pre-existing folder with the same name
        delete_url_cleanup = f"{SERVER_URL}/job/{folder_name}/delete"
        print(f"Attempting to clean up pre-existing folder '{folder_name}' via MCP server at {delete_url_cleanup}")
        try: # Inner try for cleanup - correctly indented
            cleanup_response = requests.post(delete_url_cleanup, headers=AUTH_POST_HEADERS_JSON, timeout=10)
            if cleanup_response.status_code == 200:
                print(f"Pre-existing folder '{folder_name}' cleaned up successfully.")
            elif cleanup_response.status_code == 404:
                print(f"No pre-existing folder '{folder_name}' found for cleanup.")
            else:
                print(f"Warning: Unexpected status code during cleanup of '{folder_name}': {cleanup_response.status_code}. Response: {cleanup_response.text}")
        except requests.RequestException as e: # Inner except - correctly indented
            print(f"Warning: Request error during cleanup of '{folder_name}': {e}")
        except Exception as e: # Inner except - correctly indented
            print(f"Warning: An unexpected error occurred during cleanup of '{folder_name}': {e}")

        create_url = f"{SERVER_URL}/folder/create" # Correctly indented under outer try

        # Retry logic for folder creation
        max_create_retries = 5
        create_retry_delay = 2 # seconds
        created_successfully = False

        for i in range(max_create_retries): # Correctly indented under outer try
            print(f"Attempting to create folder '{folder_name}' via MCP server at {create_url} (Attempt {i+1}/{max_create_retries})")
            try: # Try for create_response - correctly indented
                create_response = requests.post(create_url, headers=AUTH_POST_HEADERS_JSON, json=create_payload, timeout=10)
                create_response.raise_for_status()
                assert create_response.status_code == 201, f"Failed to create folder via MCP server. Status code: {create_response.status_code}. Response: {create_response.text}"
                print(f"Folder '{folder_name}' created successfully via MCP server.")
                created_successfully = True
                break # Exit retry loop on success
            except requests.exceptions.HTTPError as e: # Correctly indented
                if e.response.status_code == 409: # Conflict - likely already exists
                    print(f"Folder '{folder_name}' already exists (status 409). Assuming it exists and proceeding with verification.")
                    created_successfully = True # Treat already exists as success for creation step
                    break # Exit retry loop
                else:
                    print(f"HTTP error during folder creation (Attempt {i+1}): {e}. Retrying...")
            except requests.RequestException as e: # Correctly indented
                print(f"Request error during folder creation (Attempt {i+1}): {e}. Retrying...")
            except Exception as e: # Correctly indented
                print(f"An unexpected error occurred during folder creation (Attempt {i+1}): {e}")

            if i < max_create_retries - 1: # Correctly indented
                time.sleep(create_retry_delay)

        assert created_successfully, f"Failed to create folder '{folder_name}' after {max_create_retries} attempts." # Correctly indented

        # We might need to wait a moment for Jenkins to register the folder
        time.sleep(10) # Give Jenkins more time # Correctly indented

        # Verify the folder exists via MCP server's list jobs endpoint
        verify_exists = False
        max_verify_retries = 15 # Increased retries for verification
        verify_retry_delay = 2 # seconds

        list_jobs_url_base = f"{SERVER_URL}/jobs?recursive=true" # Use recursive to find it anywhere

        for i in range(max_verify_retries): # Correctly indented
            try: # Try for list_response - correctly indented
                # Add cache-busting parameter
                list_jobs_url_with_buster = f"{list_jobs_url_base}&_cb={time.time_ns()}"
                print(f"Verifying folder '{folder_name}' existence via MCP server at {list_jobs_url_with_buster} (Attempt {i+1}/{max_verify_retries})")
                list_response = requests.get(list_jobs_url_with_buster, timeout=10, headers=AUTH_REQUEST_HEADERS)
                list_response.raise_for_status()
                assert list_response.status_code == 200, f"Failed to list jobs via MCP server for verification. Status code: {list_response.status_code}. Response: {list_response.text}"

                jobs_data = list_response.json().get("jobs", [])
                for item in jobs_data: # Correctly indented
                    if item.get("name") == folder_name and item.get("type") == "folder": # Check for folder name and type
                        verify_exists = True
                        print(f"Folder '{folder_name}' verified to exist via MCP server listing.")
                        break

                if verify_exists: # Correctly indented
                    break # Exit retry loop on success

            except requests.RequestException as e: # Correctly indented
                print(f"Request error during folder verification (Attempt {i+1}): {e}. Retrying...")
            except Exception as e: # Correctly indented
                print(f"An unexpected error occurred during folder verification (Attempt {i+1}): {e}. Retrying...")

            if i < max_verify_retries - 1: # Correctly indented
                time.sleep(verify_retry_delay)

        assert verify_exists, f"Folder '{folder_name}' not found in MCP server job listing after creation/assumption of existence and {max_verify_retries} verification attempts." # Correctly indented
    except requests.RequestException as e: # Outer except - correctly indented
        pytest.fail(f"Test failed during folder creation or verification via MCP server: {e}")
    except Exception as e: # Outer except - correctly indented
        pytest.fail(f"An unexpected error occurred during folder test execution: {e}")
    finally: # Outer finally - correctly indented
        # Clean up: Delete the folder via MCP server
        delete_url = f"{SERVER_URL}/job/{folder_name}/delete" # Use folder_name for deletion
        print(f"Attempting to delete folder '{folder_name}' via MCP server at {delete_url}")

        # Retry logic for folder deletion
        max_delete_retries = 5
        delete_retry_delay = 2 # seconds
        deleted_successfully = False

        for i in range(max_delete_retries): # Correctly indented
            print(f"Attempting to delete folder '{folder_name}' via MCP server at {delete_url} (Attempt {i+1}/{max_delete_retries})")
            try: # Try for delete_response - correctly indented
                delete_response = requests.post(delete_url, headers=AUTH_POST_HEADERS_JSON, timeout=10)
                delete_response.raise_for_status()
                assert delete_response.status_code == 200, f"Failed to delete folder '{folder_name}' via MCP server during cleanup. Status code: {delete_response.status_code}. Response: {delete_response.text}"
                print(f"Folder '{folder_name}' deleted successfully via MCP server.")
                deleted_successfully = True
                break # Exit retry loop on success
            except requests.exceptions.HTTPError as e: # Correctly indented
                if e.response.status_code == 404: # Not Found - might be a transient state after creation failure
                     print(f"Folder '{folder_name}' not found for deletion (status 404). This might be a transient state. Retrying...")
                else:
                    print(f"HTTP error during folder deletion (Attempt {i+1}): {e}. Retrying...")
            except requests.RequestException as e: # Correctly indented
                print(f"Request error during folder deletion (Attempt {i+1}): {e}. Retrying...")
            except Exception as e: # Correctly indented
                print(f"An unexpected error occurred during folder deletion (Attempt {i+1}): {e}")

            if i < max_delete_retries - 1: # Correctly indented
                time.sleep(delete_retry_delay)

        if not deleted_successfully: # Correctly indented
            print(f"Warning: Failed to delete folder '{folder_name}' after {max_delete_retries} attempts during cleanup.")
