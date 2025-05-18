import os
import requests
import json
import sys # For stderr

# Consider making these configurable, e.g., via environment variables for OpenWebUI deployment
MCP_SERVER_URL = os.environ.get("MCP_JENKINS_SERVER_URL", "http://localhost:5000")
MCP_API_KEY = os.environ.get('MCP_API_KEY')

class Tools:
    def __init__(self):
        """
        Tools for interacting with a Jenkins MCP server.
        """
        self.mcp_server_url = MCP_SERVER_URL
        self.mcp_api_key = MCP_API_KEY
        self._ensure_api_key_warning()

    def _ensure_api_key_warning(self):
        if not self.mcp_api_key:
            print("Warning: MCP_API_KEY not found. Requests to MCP server might be unauthorized.", file=sys.stderr)

    def _call_mcp_server(self, endpoint: str, method: str = "GET", data: dict = None) -> dict | str:
        """
        Helper method to call the MCP Jenkins server.
        :param endpoint: The API endpoint to call (e.g., "/jobs").
        :param method: HTTP method ("GET", "POST").
        :param data: JSON payload for POST requests.
        :return: Parsed JSON response from the server or an error message string.
        """
        headers = {}
        if self.mcp_api_key:
            headers['X-API-Key'] = self.mcp_api_key

        url = f"{self.mcp_server_url}{endpoint}"
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
            error_details = ""
            try:
                error_payload = e.response.json()
                if "error" in error_payload:
                    error_details = f" Server message: {error_payload['error']}"
            except ValueError:
                error_details = f" Server response: {e.response.text}"
            return f"Error calling MCP server ({e.response.status_code} {e.response.reason}) for {url}.{error_details}"
        except requests.exceptions.RequestException as e:
            return f"Error calling MCP server: {e}"
        except json.JSONDecodeError:
            return f"Error parsing MCP server JSON response. Response: {mcp_response.text if 'mcp_response' in locals() else 'No response object'}"

    def get_build_status(self, job_name: str, build_number: str | int) -> str:
        """
        Get the status of a specific Jenkins build.
        :param job_name: The name of the Jenkins job (e.g., "MyProject/main").
        :type job_name: str
        :param build_number: The build number (e.g., 15, "lastBuild", "lastSuccessfulBuild").
        :type build_number: str | int
        :return: A string describing the build status or an error message.
        :rtype: str
        """
        if not job_name or build_number is None:
            return "Error: Missing job_name or build_number for get_build_status."
        
        result = self._call_mcp_server(f"/job/{job_name}/build/{build_number}")
        if isinstance(result, dict):
            is_building = result.get('building')
            build_result_status = result.get('result')
            status = "UNKNOWN"
            if is_building:
                status = "BUILDING"
            elif build_result_status:
                status = build_result_status
            return f"Status for {job_name} build {result.get('build_number', build_number)}: {status}. URL: {result.get('url')}"
        return str(result) # Return error string from _call_mcp_server

    def list_job_builds(self, job_name: str, limit: int = 5) -> str:
        """
        List recent builds for a Jenkins job.
        :param job_name: The name of the Jenkins job (e.g., "MyProject/main").
        :type job_name: str
        :param limit: The maximum number of recent builds to list. Defaults to 5.
        :type limit: int
        :return: A summary of recent builds or an error message.
        :rtype: str
        """
        if not job_name:
            return "Error: Missing job_name for list_job_builds."
        
        builds_data = self._call_mcp_server(f"/job/{job_name}/builds")
        if isinstance(builds_data, dict) and "builds" in builds_data:
            builds = builds_data["builds"]
            if not builds:
                return f"No builds found for job {job_name}."
            summary = f"Recent builds for {job_name} (limit {limit}):\n"
            for build in builds[:limit]:
                status = "BUILDING" if build.get('building') else build.get('result', 'UNKNOWN')
                summary += f"  - Build #{build['number']}: {status} ({build['url']})\n"
            return summary.strip()
        return str(builds_data)

    def trigger_build(self, job_name: str, build_parameters: dict = None) -> str:
        """
        Trigger a new build for a Jenkins job, optionally with parameters.
        :param job_name: The name of the Jenkins job to build (e.g., "MyProject/main").
        :type job_name: str
        :param build_parameters: A dictionary of parameters to pass to the build. Defaults to None.
        :type build_parameters: dict, optional
        :return: A message indicating the build trigger status or an error message.
        :rtype: str
        """
        if not job_name:
            return "Error: Missing job_name for trigger_build."
        
        payload_for_server = {"parameters": build_parameters if build_parameters else {}}
        result = self._call_mcp_server(f"/job/{job_name}/build", method="POST", data=payload_for_server)
        if isinstance(result, dict) and "message" in result:
            return f"{result['message']} for {job_name}. Queue item: {result.get('queue_item_url') or result.get('queue_item', 'N/A')}"
        return str(result)

    def list_jobs(self, folder_name: str = None, recursive: bool = False) -> str:
        """
        List Jenkins jobs, optionally filtered by folder and recursion.
        :param folder_name: The name of the folder to list jobs from. Defaults to None (root).
        :type folder_name: str, optional
        :param recursive: Whether to list jobs recursively within folders. Defaults to False.
        :type recursive: bool
        :return: A summary of available jobs or an error message.
        :rtype: str
        """
        endpoint = "/jobs"
        query_params = []
        if folder_name:
            query_params.append(f"folder_name={requests.utils.quote(folder_name)}")
        if recursive:
            query_params.append(f"recursive=true")
        if query_params:
            endpoint += "?" + "&".join(query_params)

        jobs_data = self._call_mcp_server(endpoint)
        if isinstance(jobs_data, dict) and "jobs" in jobs_data:
            jobs = jobs_data["jobs"]
            if not jobs:
                return "No jobs found with the given criteria."
            summary = "Available jobs:\n"
            for job in jobs[:20]: # Limit output for brevity
                summary += f"  - {job['name']} (Class: {job.get('_class', 'N/A')}, URL: {job.get('url', 'N/A')})\n"
            if len(jobs) > 20:
                summary += f"... and {len(jobs)-20} more."
            return summary.strip()
        return str(jobs_data)

    def get_build_log(self, job_name: str, build_number: str | int) -> str:
        """
        Get the console output (log) of a specific Jenkins build.
        Provides a summary and a URL to the full log.
        :param job_name: The name of the Jenkins job (e.g., "MyProject/main").
        :type job_name: str
        :param build_number: The build number (e.g., 15, "lastBuild").
        :type build_number: str | int
        :return: A summary of the build log and a link to the full log, or an error message.
        :rtype: str
        """
        if not job_name or build_number is None:
            return "Error: Missing job_name or build_number for get_build_log."
        
        result = self._call_mcp_server(f"/job/{job_name}/build/{build_number}/log")
        if isinstance(result, dict) and "summary" in result and "log_url" in result:
            return (f"Log summary for {result.get('job_name', job_name)} build {result.get('build_number', build_number)}:\n"
                    f"{result['summary']}\n"
                    f"Full log available at: {result['log_url']}")
        elif isinstance(result, dict) and "error" in result:
             return f"Error from server: {result['error']}"
        return str(result)

    def create_job(self, job_name: str, job_type: str, job_description: str = None, 
                   month: int = None, year: int = None, city: str = None) -> str:
        """
        Create a new Jenkins job. Currently supports 'calendar' or 'weather' job types.
        For 'calendar' jobs, 'month' and 'year' are required.
        For 'weather' jobs, 'city' is required.
        
        :param job_name: The desired name for the new job.
        :type job_name: str
        :param job_type: The type of job to create ('calendar' or 'weather').
        :type job_type: str
        :param job_description: An optional description for the job.
        :type job_description: str, optional
        :param month: The month (1-12) for 'calendar' jobs.
        :type month: int, optional
        :param year: The year (e.g., 2024) for 'calendar' jobs.
        :type year: int, optional
        :param city: The city name for 'weather' jobs.
        :type city: str, optional
        :return: A message indicating success or failure of job creation.
        :rtype: str
        """
        if not job_name:
            return "Error: Job name cannot be empty."
        if job_type not in ["calendar", "weather"]:
            return "Error: Invalid job_type. Must be 'calendar' or 'weather'."

        payload_for_server = {
            "job_name": job_name,
            "job_type": job_type,
            "job_description": job_description,
            "month": month,
            "year": year,
            "city": city
        }
        
        # Validate required parameters based on job_type
        if job_type == "calendar":
            if month is None or year is None:
                return "Error: For 'calendar' job_type, 'month' and 'year' parameters are required."
        elif job_type == "weather":
            if not city:
                return "Error: For 'weather' job_type, 'city' parameter is required."

        # Filter out None values as server Pydantic models handle Optional fields
        payload_for_server = {k: v for k, v in payload_for_server.items() if v is not None}

        result = self._call_mcp_server("/job/create", method="POST", data=payload_for_server)
        if isinstance(result, dict) and "message" in result:
            return f"{result['message']}. Job: {result.get('job_name')}, URL: {result.get('job_url')}"
        elif isinstance(result, dict) and "error" in result:
             return f"Error from server: {result['error']}"
        return str(result)

    # Example of a tool from the provided OpenWebUI snippet, can be removed if not relevant
    def get_current_time(self, __user__: dict = {}) -> str:
        """
        Get the current time in a human-readable format.
        :return: The current time.
        :rtype: str
        """
        # Do not include :param for __user__ in the docstring as it should not be shown
        from datetime import datetime
        now = datetime.now()
        current_time = now.strftime("%I:%M:%S %p")
        current_date = now.strftime("%A, %B %d, %Y")
        return f"Current Date and Time = {current_date}, {current_time}"

# To test a specific tool method locally (example):
if __name__ == '__main__':
    tools_instance = Tools()
    
    # Ensure MCP_API_KEY is set in your environment if required by your server
    # Ensure MCP_JENKINS_SERVER_URL is set or defaults to http://localhost:5000
    
    print("Attempting to list jobs...")
    # Replace with your actual Jenkins job name for testing other functions
    # print(tools_instance.list_jobs(recursive=True))
    # print(tools_instance.get_build_status(job_name="folder/my-job", build_number="lastBuild"))
    # print(tools_instance.trigger_build(job_name="test-job"))
    # print(tools_instance.create_job(job_name="TestWebUIJob", job_type="calendar", month=12, year=2025, job_description="A test job from WebUI"))
    # print(tools_instance.get_build_log(job_name="TestWebUIJob", build_number=1))

    # Example for get_current_time
    # print(tools_instance.get_current_time())
    
    # Test list_jobs
    # print("\n--- Testing list_jobs ---")
    # print(tools_instance.list_jobs())
    
    # Test create_job (calendar)
    # print("\n--- Testing create_job (calendar) ---")
    # print(tools_instance.create_job(job_name="WebUICalendarJob", job_type="calendar", month=7, year=2024, job_description="Calendar job via WebUI"))
    
    # Test list_job_builds (assuming WebUICalendarJob might not have builds yet)
    # print("\n--- Testing list_job_builds for WebUICalendarJob ---")
    # print(tools_instance.list_job_builds(job_name="WebUICalendarJob"))

    # Test trigger_build (for a job that can be triggered, e.g., a freestyle or pipeline job)
    # print("\n--- Testing trigger_build for 'freestyle-project-test' ---") # Replace with an actual job name
    # print(tools_instance.trigger_build(job_name="freestyle-project-test")) 
    
    # After triggering, you might want to check status or log
    # print("\n--- Testing get_build_status for 'freestyle-project-test' build 'lastBuild' ---")
    # print(tools_instance.get_build_status(job_name="freestyle-project-test", build_number="lastBuild"))
    
    # print("\n--- Testing get_build_log for 'freestyle-project-test' build 'lastBuild' ---")
    # print(tools_instance.get_build_log(job_name="freestyle-project-test", build_number="lastBuild"))

    # Example of listing jobs in a folder
    # print(tools_instance.list_jobs(folder_name="my_folder", recursive=True))
    pass # Add more specific tests as needed
