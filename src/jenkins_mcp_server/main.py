import os
import logging
from functools import wraps
from flask import Flask, request, jsonify
import jenkins

# --- Configuration ---
JENKINS_URL = os.environ.get('JENKINS_URL')
JENKINS_USER = os.environ.get('JENKINS_USER')
JENKINS_API_TOKEN = os.environ.get('JENKINS_API_TOKEN')
MCP_API_KEY = os.environ.get('MCP_API_KEY') # For securing this MCP server
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Logging Setup ---
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Input Validation ---
if not all([JENKINS_URL, JENKINS_USER, JENKINS_API_TOKEN]):
    logger.critical("Jenkins credentials (JENKINS_URL, JENKINS_USER, JENKINS_API_TOKEN) not found in environment variables.")
    raise ValueError("Jenkins credentials not found in environment variables.")

if not MCP_API_KEY:
    logger.warning("MCP_API_KEY is not set. The server will be unsecured. This is not recommended for production.")
    # For development, you might allow it to run, but for production, you might want to raise an error:
    # raise ValueError("MCP_API_KEY not found in environment variables. Server will not start.")


# --- Jenkins Server Connection ---
try:
    jenkins_server = jenkins.Jenkins(JENKINS_URL, username=JENKINS_USER, password=JENKINS_API_TOKEN, timeout=10)
    jenkins_server.get_whoami() # Test connection
    logger.info(f"Successfully connected to Jenkins server at {JENKINS_URL}")
except jenkins.JenkinsException as e:
    logger.critical(f"Failed to connect to Jenkins: {e}")
    raise  # Re-raise to prevent app from starting if Jenkins connection fails initially
except Exception as e:
    logger.critical(f"An unexpected error occurred during Jenkins initialization: {e}")
    raise


# --- Authentication Decorator ---
def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not MCP_API_KEY: # If no key is set, bypass auth (useful for local dev, but insecure)
            logger.warning("MCP_API_KEY not set, skipping authentication. NOT FOR PRODUCTION.")
            return f(*args, **kwargs)

        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key != MCP_API_KEY:
            logger.warning(f"Unauthorized access attempt from IP: {request.remote_addr}")
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

# --- Helper for Standard Error Response ---
def make_error_response(message, status_code):
    return jsonify({"error": message, "status_code": status_code}), status_code

# --- Routes ---
@app.route('/')
def hello():
    """Greets the user."""
    logger.info(f"Root endpoint accessed by {request.remote_addr}")
    return "Hello from Jenkins MCP server!"

@app.route('/health')
def health_check():
    """Provides a health check for the service and Jenkins connection."""
    try:
        jenkins_server.get_whoami()
        jenkins_status = "connected"
        status_code = 200
    except jenkins.JenkinsException as e:
        logger.error(f"Health check: Jenkins connection error: {e}")
        jenkins_status = f"disconnected - {str(e)}"
        status_code = 503 # Service Unavailable
    except Exception as e:
        logger.error(f"Health check: Unexpected error: {e}")
        jenkins_status = f"error - {str(e)}"
        status_code = 500

    return jsonify({
        "mcp_server_status": "ok",
        "jenkins_connection": jenkins_status
    }), status_code


@app.route('/jobs', methods=['GET'])
@require_api_key
def list_jobs():
    """
    Lists all jobs or jobs within a specified folder.
    Query Parameter:
        folder_name (optional): The name of the folder to list jobs from.
                                Example: 'MyPipelineFolder' or 'FolderA/SubFolderB'
    """
    folder_name = request.args.get('folder_name')
    try:
        if folder_name:
            logger.info(f"Listing jobs for folder: {folder_name}")
            # python-jenkins uses folder_name/job/job_name format for jobs in folders
            # To list jobs *within* a folder, we need to get the folder info first
            # This can be complex if deeply nested. A simpler approach might be to expect
            # the user to know the full job path if they want specific job details.
            # For now, let's assume get_jobs() can handle some level of folder path.
            # Alternatively, for specific folder content, get_job_info for the folder and parse.
            # This is an area where python-jenkins is a bit tricky for deeply nested folders.
            # For simplicity, let's list top-level jobs or jobs if folder_name is given.
            # A more robust way for deep folders might require multiple calls or a different library feature.
            jobs_data = jenkins_server.get_jobs(folder_depth=0, folder_name=folder_name) # folder_depth might be needed
        else:
            logger.info("Listing all top-level jobs")
            jobs_data = jenkins_server.get_jobs(folder_depth=0)

        jobs = [{"name": job['fullname'] if 'fullname' in job else job['name'], "url": job['url']} for job in jobs_data]
        return jsonify({"jobs": jobs})
    except jenkins.NotFoundException:
        logger.warning(f"Folder '{folder_name}' not found.")
        return make_error_response(f"Folder '{folder_name}' not found", 404)
    except jenkins.JenkinsException as e:
        logger.error(f"Jenkins API error while listing jobs for folder '{folder_name}': {e}")
        return make_error_response(f"Jenkins API error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Unexpected error while listing jobs for folder '{folder_name}': {e}")
        return make_error_response(f"An unexpected error occurred: {str(e)}", 500)


@app.route('/job/<path:job_path>/builds', methods=['GET'])
@require_api_key
def list_job_builds(job_path):
    """
    Lists all build numbers for a given job.
    The job_path can include folders, e.g., 'MyJob' or 'MyFolder/MyJob'.
    """
    if not job_path:
        logger.warning("List builds request with missing job_path.")
        return make_error_response("Missing job_path parameter", 400)

    logger.info(f"Listing builds for job: {job_path}")
    try:
        job_info = jenkins_server.get_job_info(job_path)
        builds = []
        for build in job_info.get('builds', []):
            build_details = jenkins_server.get_build_info(job_path, build['number'])
            builds.append({
                "number": build_details['number'],
                "url": build_details['url'],
                "timestamp": build_details['timestamp'],
                "duration": build_details['duration'],
                "result": build_details.get('result'), # Can be None if running
                "building": build_details['building']
            })
        return jsonify({"job_name": job_path, "builds": builds})
    except jenkins.NotFoundException:
        logger.warning(f"Job '{job_path}' not found when listing builds.")
        return make_error_response(f"Job '{job_path}' not found", 404)
    except jenkins.JenkinsException as e:
        logger.error(f"Jenkins API error for job '{job_path}' builds: {e}")
        return make_error_response(f"Jenkins API error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Unexpected error for job '{job_path}' builds: {e}")
        return make_error_response(f"An unexpected error occurred: {str(e)}", 500)


@app.route('/job/<path:job_path>/build/<build_number_str>', methods=['GET'])
@require_api_key
def get_build_status(job_path, build_number_str):
    """
    Gets the status of a specific build for a job.
    job_path can include folders, e.g., 'MyJob' or 'MyFolder/MyJob'.
    build_number_str should be the build number or 'lastBuild', 'lastSuccessfulBuild', etc.
    """
    if not job_path or not build_number_str:
        logger.warning("Build status request with missing job_path or build_number.")
        return make_error_response("Missing job_path or build_number parameter", 400)

    try:
        # Allow string identifiers like 'lastBuild' or convert to int
        if build_number_str.isdigit():
            build_identifier = int(build_number_str)
        else:
            # For strings like 'lastBuild', 'lastSuccessfulBuild', etc.
            # We first get job info, then extract the actual build number from there.
            job_info = jenkins_server.get_job_info(job_path)
            if build_number_str in job_info and isinstance(job_info[build_number_str], dict) and 'number' in job_info[build_number_str]:
                build_identifier = job_info[build_number_str]['number']
            else: # Try to get build info directly if python-jenkins supports it
                logger.info(f"Attempting to fetch build '{build_number_str}' for job '{job_path}' directly.")
                # This might not work for all string identifiers with get_build_info.
                # The most reliable way for 'lastBuild' etc. is to get job_info first.
                # However, get_build_info with a number is the direct path.
                return make_error_response(f"Invalid build identifier string: {build_number_str}", 400)

        logger.info(f"Getting status for job '{job_path}', build #{build_identifier}")
        build_info = jenkins_server.get_build_info(job_path, build_identifier)

        status_details = {
            "job_name": job_path,
            "build_number": build_info['number'],
            "url": build_info['url'],
            "building": build_info['building'],
            "result": build_info.get('result'),  # 'SUCCESS', 'FAILURE', 'ABORTED', 'UNSTABLE', or None if building
            "timestamp": build_info['timestamp'],
            "duration": build_info['duration'], # in milliseconds
            "estimated_duration": build_info['estimatedDuration'],
            "description": build_info.get('description'),
            "full_display_name": build_info.get('fullDisplayName')
        }

        return jsonify(status_details)

    except jenkins.NotFoundException:
        logger.warning(f"Job '{job_path}' or build '{build_number_str}' not found.")
        return make_error_response(f"Job '{job_path}' or build '{build_number_str}' not found", 404)
    except jenkins.JenkinsException as e:
        logger.error(f"Jenkins API error for job '{job_path}', build '{build_number_str}': {e}")
        return make_error_response(f"Jenkins API error: {str(e)}", 500)
    except ValueError:
        logger.warning(f"Invalid build_number format: {build_number_str} for job {job_path}")
        return make_error_response(f"Invalid build_number format: {build_number_str}. Must be an integer or a valid string identifier.", 400)
    except Exception as e:
        logger.error(f"Unexpected error for job '{job_path}', build '{build_number_str}': {e}")
        return make_error_response(f"An unexpected error occurred: {str(e)}", 500)


@app.route('/job/<path:job_path>/build', methods=['POST'])
@require_api_key
def trigger_build(job_path):
    """
    Triggers a new build for the specified job.
    Accepts JSON body for parameters if any.
    Example: curl -X POST -H "Content-Type: application/json" -H "X-API-Key: yourkey" \
             -d '{"param1": "value1", "GIT_BRANCH": "develop"}' \
             http://localhost:5000/job/MyFolder/MyJob/build
    """
    if not job_path:
        logger.warning("Trigger build request with missing job_path.")
        return make_error_response("Missing job_path parameter", 400)

    params = request.get_json(silent=True) or {}
    logger.info(f"Triggering build for job: {job_path} with parameters: {params}")

    try:
        # Check if job exists and is buildable
        try:
            job_info = jenkins_server.get_job_info(job_path)
            if not job_info.get('buildable'):
                logger.warning(f"Attempted to build non-buildable job: {job_path}")
                return make_error_response(f"Job '{job_path}' is not buildable.", 400)
        except jenkins.NotFoundException:
            logger.warning(f"Job '{job_path}' not found for triggering build.")
            return make_error_response(f"Job '{job_path}' not found.", 404)


        # The python-jenkins build_job method returns a queue item number.
        # We might want to wait for the build to actually start and get its number.
        queue_item_number = jenkins_server.build_job(job_path, parameters=params)
        logger.info(f"Job '{job_path}' added to build queue. Queue item: {queue_item_number}")

        # Optionally, you can try to get the build number from the queue item
        # This can take a moment for the build to start.
        # build_number = None
        # try:
        #     queue_item_info = jenkins_server.get_queue_item(queue_item_number)
        #     if 'executable' in queue_item_info and 'number' in queue_item_info['executable']:
        #         build_number = queue_item_info['executable']['number']
        #         logger.info(f"Build for job '{job_path}' started as build #{build_number}")
        # except Exception as e:
        #     logger.warning(f"Could not retrieve build number from queue item {queue_item_number} immediately: {e}")


        return jsonify({
            "message": "Build triggered successfully",
            "job_name": job_path,
            "parameters": params,
            "queue_item": queue_item_number,
            # "build_number": build_number # if retrieved
        }), 202 # Accepted
    except jenkins.NotFoundException: # Should be caught above, but as a safeguard
        logger.warning(f"Job '{job_path}' not found when trying to trigger build.")
        return make_error_response(f"Job '{job_path}' not found", 404)
    except jenkins.JenkinsException as e:
        logger.error(f"Jenkins API error triggering build for job '{job_path}': {e}")
        return make_error_response(f"Jenkins API error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Unexpected error triggering build for job '{job_path}': {e}")
        return make_error_response(f"An unexpected error occurred: {str(e)}", 500)


if __name__ == '__main__':
    # For development only. Use a proper WSGI server (e.g., Gunicorn) for production.
    logger.info("Starting Flask development server.")
    app.run(debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true', host='0.0.0.0', port=5000)