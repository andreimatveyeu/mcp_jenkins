import os
import logging
from functools import wraps
from flask import Flask, request, jsonify
import jenkins
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError
from cachetools import TTLCache
from pydantic import BaseModel, ValidationError
from typing import Optional, Dict, Any

# --- Configuration ---
JENKINS_URL = os.environ.get('JENKINS_URL')
JENKINS_USER = os.environ.get('JENKINS_USER')
JENKINS_API_TOKEN = os.environ.get('JENKINS_API_TOKEN')
MCP_API_KEY = os.environ.get('MCP_API_KEY') # For securing this MCP server
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
DEBUG_MODE = os.environ.get('DEBUG_MODE', 'False').lower() == 'true'

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Rate Limiting Setup ---
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour", "10 per minute"],
    storage_uri="memory://", # Use "redis://localhost:6379" or other persistent storage for production
    strategy="fixed-window" # or "moving-window"
)

# --- Logging Setup ---
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Input Validation ---
if not all([JENKINS_URL, JENKINS_USER, JENKINS_API_TOKEN]):
    logger.critical("Jenkins credentials (JENKINS_URL, JENKINS_USER, JENKINS_API_TOKEN) not found in environment variables.")
    raise ValueError("Jenkins credentials not found in environment variables.")

if not MCP_API_KEY and not DEBUG_MODE:
    logger.critical("MCP_API_KEY is not set and DEBUG_MODE is false. Server will not start in secure mode without an API key.")
    raise ValueError("MCP_API_KEY not found in environment variables and not in DEBUG_MODE. Server will not start.")
elif not MCP_API_KEY and DEBUG_MODE:
    logger.warning("MCP_API_KEY is not set, but DEBUG_MODE is true. Server will run unsecured.")
elif MCP_API_KEY and DEBUG_MODE:
    logger.info("MCP_API_KEY is set, and DEBUG_MODE is true.")
else: # MCP_API_KEY is set and DEBUG_MODE is false
    logger.info("MCP_API_KEY is set. Server running in secure mode.")


# --- Jenkins Server Connection ---
# Adding tenacity for retries
@retry(wait=wait_exponential(multiplier=1, min=4, max=10), stop=stop_after_attempt(5))
def connect_to_jenkins():
    logger.info(f"Attempting to connect to Jenkins server at {JENKINS_URL}...")
    server = jenkins.Jenkins(JENKINS_URL, username=JENKINS_USER, password=JENKINS_API_TOKEN, timeout=20) # Increased timeout
    server.get_whoami() # Test connection
    return server

try:
    jenkins_server = connect_to_jenkins()
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
        if DEBUG_MODE and not MCP_API_KEY:
            logger.warning("DEBUG_MODE: MCP_API_KEY not set, skipping authentication.")
            return f(*args, **kwargs)
        
        if not MCP_API_KEY: # Should not happen if initial check is robust and DEBUG_MODE is false
            logger.error("CRITICAL: MCP_API_KEY is not configured, but authentication is being attempted. This indicates a misconfiguration.")
            return jsonify({"error": "Server configuration error: API key not set"}), 500

        api_key = request.headers.get('X-API-Key')
        if not api_key or api_key != MCP_API_KEY:
            logger.warning(f"Unauthorized access attempt from IP: {request.remote_addr}")
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

# --- Caching Setup ---
# Cache for job listings (e.g., 5 minutes TTL, max 100 entries)
job_list_cache = TTLCache(maxsize=100, ttl=300)
# Cache for job build lists (e.g., 1 minute TTL, max 200 entries)
job_builds_cache = TTLCache(maxsize=200, ttl=60)
# Cache for individual build status (e.g., 30 seconds TTL, max 500 entries)
build_status_cache = TTLCache(maxsize=500, ttl=30)


# --- Pydantic Models for Input Validation ---
class BuildJobPayload(BaseModel):
    # Allows any parameters, Jenkins handles specifics.
    # For stricter validation, define known parameters or use Dict[str, Union[str, int, bool]]
    parameters: Optional[Dict[str, Any]] = None
    # Example of a specific known parameter:
    # GIT_BRANCH: Optional[str] = None

# --- Helper for Standard Error Response ---
def make_error_response(message, status_code):
    return jsonify({"error": message, "status_code": status_code}), status_code

# --- Routes ---
@app.route('/')
@limiter.limit("5 per minute") # Example: limit root separately
def hello():
    """Greets the user."""
    logger.info(f"Root endpoint accessed by {request.remote_addr}")
    return "Hello from Jenkins MCP server!"

@app.route('/health')
@limiter.limit("10 per minute") # Example: limit health check
def health_check():
    """Provides a health check for the service and Jenkins connection."""
    try:
        # Use a direct, non-retrying call for health check to get current status
        jenkins_server.get_whoami() # Test connection without retry for health status
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

# Helper function for recursive job listing
def _get_and_filter_jobs_recursively(all_server_items, current_folder_prefix, depth, max_allowed_depth):
    if depth > max_allowed_depth:
        logger.debug(f"Max recursion depth {max_allowed_depth} reached for prefix '{current_folder_prefix}'. Stopping this path.")
        return []
    
    local_jobs = []
    logger.debug(f"Filtering for children of '{current_folder_prefix if current_folder_prefix else 'root'}' at depth {depth}. Total items to scan: {len(all_server_items)}")

    for item in all_server_items:
        item_fullname = item.get('fullname', item.get('name'))
        item_url = item.get('url')
        item_class = item.get('_class', '')

        if not item_fullname:
            logger.warning(f"Skipping item with no fullname/name: {item}")
            continue

        is_folder = 'folder' in item_class.lower() or 'multibranch' in item_class.lower()
        is_relevant_child = False
        
        if current_folder_prefix: # We are looking for children of a specific folder
            if item_fullname.startswith(current_folder_prefix + '/'):
                relative_name = item_fullname[len(current_folder_prefix) + 1:]
                if '/' not in relative_name: # Direct child
                    is_relevant_child = True
        else: # We are looking for children of the root
            if '/' not in item_fullname: # Top-level item
                is_relevant_child = True
        
        if is_relevant_child:
            item_representation = {"name": item_fullname, "url": item_url, "_class": item_class}
            if is_folder:
                item_representation["type"] = "folder"
            local_jobs.append(item_representation)
            
            if is_folder and depth < max_allowed_depth: # Only recurse if it's a folder and we haven't hit max depth
                logger.info(f"Recursively processing identified folder: {item_fullname} (current depth {depth}, max_allowed_depth {max_allowed_depth})")
                nested_jobs = _get_and_filter_jobs_recursively(
                    all_server_items,       # Pass the same full list
                    item_fullname,          # New prefix is the current folder's fullname
                    depth + 1,              # Increment depth
                    max_allowed_depth       # Pass along max_allowed_depth
                )
                local_jobs.extend(nested_jobs)
    return local_jobs

@app.route('/jobs', methods=['GET'])
@require_api_key
@limiter.exempt 
def list_jobs():
    """
    Lists all jobs, optionally filtering by a base folder and performing a recursive search.
    Query Parameter:
        folder_name (optional): The base folder name to start listing from.
                                If not provided, lists all jobs from the root.
        recursive (optional): 'true' or 'false' (default 'false').
                              If 'true', recursively lists jobs in sub-folders.
    """
    folder_name = request.args.get('folder_name')
    recursive_str = request.args.get('recursive', 'false').lower()
    recursive = recursive_str == 'true'

    cache_key = f"list_jobs::{folder_name}::recursive={recursive}"
    cached_result = job_list_cache.get(cache_key)
    if cached_result:
        logger.info(f"Returning cached job list for key: {cache_key}")
        return jsonify({"jobs": cached_result, "source": "cache"})

    try:
        @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), reraise=True)
        def fetch_all_jenkins_items_from_server():
            logger.info("Fetching all jobs/items from Jenkins server (this might take a moment)...")
            # This is the crucial call that fetches everything.
            # The comment about python-jenkins 1.8.2 not taking folder_name for get_jobs() is key.
            return jenkins_server.get_jobs() 
        
        all_server_items_flat_list = fetch_all_jenkins_items_from_server()
        
        max_depth_for_call = 5 if recursive else 0 
        logger.info(f"Filtering all {len(all_server_items_flat_list)} Jenkins items for base folder: '{folder_name if folder_name else 'root'}', recursive: {recursive}, max_depth: {max_depth_for_call}")
        
        processed_jobs = _get_and_filter_jobs_recursively(
            all_server_items_flat_list,
            current_folder_prefix=folder_name, # Start filtering from this folder (or root if None)
            depth=0,
            max_allowed_depth=max_depth_for_call
        )
        
        # Deduplication based on fullname (should be unique)
        deduplicated_jobs = []
        seen_fullnames = set()
        for job in processed_jobs:
            if job['name'] not in seen_fullnames:
                deduplicated_jobs.append(job)
                seen_fullnames.add(job['name'])
        
        job_list_cache[cache_key] = deduplicated_jobs
        logger.info(f"Found {len(deduplicated_jobs)} jobs/folders after processing for folder '{folder_name if folder_name else 'root'}' (recursive={recursive}).")
        return jsonify({"jobs": deduplicated_jobs, "source": "api"})
    
    except RetryError as e: # Catch RetryError from fetch_all_jenkins_items_from_server
        logger.error(f"Jenkins API error after retries while fetching all jobs: {e}")
        return make_error_response(f"Jenkins API error after retries: {str(e)}", 500)
    except jenkins.JenkinsException as e: # Catch other Jenkins specific errors
        logger.error(f"Jenkins API error while listing jobs: {e}")
        return make_error_response(f"Jenkins API error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Unexpected error while listing jobs: {e}", exc_info=True) # Add exc_info for better debugging
        return make_error_response(f"An unexpected error occurred: {str(e)}", 500)


@app.route('/job/<path:job_path>/builds', methods=['GET'])
@require_api_key
@limiter.limit("60 per hour") # Example specific limit
def list_job_builds(job_path):
    """
    Lists all build numbers for a given job.
    The job_path can include folders, e.g., 'MyJob' or 'MyFolder/MyJob'.
    """
    if not job_path:
        logger.warning("List builds request with missing job_path.")
        return make_error_response("Missing job_path parameter", 400)

    logger.info(f"Listing builds for job: {job_path}")

    cache_key = f"job_builds::{job_path}"
    cached_result = job_builds_cache.get(cache_key)
    if cached_result:
        logger.info(f"Returning cached build list for job: {job_path}")
        return jsonify({"job_name": job_path, "builds": cached_result, "source": "cache"})

    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), reraise=True)
    def _fetch_job_info_with_builds(j_path):
        return jenkins_server.get_job_info(j_path)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=4), stop=stop_after_attempt(3), reraise=True)
    def _fetch_build_info(j_path, build_num):
        return jenkins_server.get_build_info(j_path, build_num)

    try:
        job_info = _fetch_job_info_with_builds(job_path)
        builds_summary = []
        for build_ref in job_info.get('builds', []):
            build_details = _fetch_build_info(job_path, build_ref['number'])
            builds_summary.append({
                "number": build_details['number'],
                "url": build_details['url'],
                "timestamp": build_details['timestamp'],
                "duration": build_details['duration'],
                "result": build_details.get('result'),
                "building": build_details['building']
            })
        
        job_builds_cache[cache_key] = builds_summary
        return jsonify({"job_name": job_path, "builds": builds_summary, "source": "api"})
    except jenkins.NotFoundException:
        logger.warning(f"Job '{job_path}' not found when listing builds.")
        return make_error_response(f"Job '{job_path}' not found", 404)
    except RetryError as e:
        logger.error(f"Jenkins API error after retries for job '{job_path}' builds: {e}")
        return make_error_response(f"Jenkins API error after retries: {str(e)}", 500)
    except jenkins.JenkinsException as e:
        logger.error(f"Jenkins API error for job '{job_path}' builds: {e}")
        return make_error_response(f"Jenkins API error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Unexpected error for job '{job_path}' builds: {e}")
        return make_error_response(f"An unexpected error occurred: {str(e)}", 500)


@app.route('/job/<path:job_path>/build/<build_number_str>', methods=['GET'])
@require_api_key
@limiter.limit("120 per hour") # Example specific limit
def get_build_status(job_path, build_number_str):
    """
    Gets the status of a specific build for a job.
    job_path can include folders, e.g., 'MyJob' or 'MyFolder/MyJob'.
    build_number_str should be the build number or 'lastBuild', 'lastSuccessfulBuild', etc.
    """
    if not job_path or not build_number_str:
        logger.warning("Build status request with missing job_path or build_number.")
        return make_error_response("Missing job_path or build_number parameter", 400)

    cache_key = f"build_status::{job_path}::{build_number_str}"
    cached_result = build_status_cache.get(cache_key)
    if cached_result:
        logger.info(f"Returning cached build status for: {cache_key}")
        return jsonify({**cached_result, "source": "cache"})

    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), reraise=True)
    def _fetch_job_info(j_path):
        return jenkins_server.get_job_info(j_path)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=4), stop=stop_after_attempt(3), reraise=True)
    def _fetch_build_info(j_path, build_id):
        return jenkins_server.get_build_info(j_path, build_id)

    try:
        build_identifier_resolved = None
        if build_number_str.isdigit():
            build_identifier_resolved = int(build_number_str)
        else:
            job_info_data = _fetch_job_info(job_path)
            if build_number_str in job_info_data and \
               isinstance(job_info_data[build_number_str], dict) and \
               'number' in job_info_data[build_number_str]:
                build_identifier_resolved = job_info_data[build_number_str]['number']
            else:
                logger.warning(f"Cannot resolve build identifier string '{build_number_str}' for job '{job_path}'.")
                return make_error_response(f"Invalid or unresolvable build identifier string: {build_number_str}", 400)
        
        if build_identifier_resolved is None: # Should be caught above, but as a safeguard
             return make_error_response(f"Could not determine build number for: {build_number_str}", 400)

        logger.info(f"Getting status for job '{job_path}', build #{build_identifier_resolved}")
        build_info_data = _fetch_build_info(job_path, build_identifier_resolved)

        status_details = {
            "job_name": job_path,
            "build_number": build_info_data['number'],
            "url": build_info_data['url'],
            "building": build_info_data['building'],
            "result": build_info_data.get('result'),
            "timestamp": build_info_data['timestamp'],
            "duration": build_info_data['duration'],
            "estimated_duration": build_info_data['estimatedDuration'],
            "description": build_info_data.get('description'),
            "full_display_name": build_info_data.get('fullDisplayName')
        }
        
        build_status_cache[cache_key] = status_details
        return jsonify({**status_details, "source": "api"})

    except jenkins.NotFoundException:
        logger.warning(f"Job '{job_path}' or build '{build_number_str}' (resolved to {build_identifier_resolved if 'build_identifier_resolved' in locals() else 'N/A'}) not found.")
        return make_error_response(f"Job '{job_path}' or build '{build_number_str}' not found", 404)
    except RetryError as e:
        logger.error(f"Jenkins API error after retries for job '{job_path}', build '{build_number_str}': {e}")
        return make_error_response(f"Jenkins API error after retries: {str(e)}", 500)
    except jenkins.JenkinsException as e:
        logger.error(f"Jenkins API error for job '{job_path}', build '{build_number_str}': {e}")
        return make_error_response(f"Jenkins API error: {str(e)}", 500)
    except ValueError: # From int(build_number_str) if not a digit and not a special string
        logger.warning(f"Invalid build_number format: {build_number_str} for job {job_path}")
        return make_error_response(f"Invalid build_number format: {build_number_str}. Must be an integer or a valid string identifier.", 400)
    except Exception as e:
        logger.error(f"Unexpected error for job '{job_path}', build '{build_number_str}': {e}")
        return make_error_response(f"An unexpected error occurred: {str(e)}", 500)


@app.route('/job/<path:job_path>/build/<build_number_str>/log', methods=['GET'])
@require_api_key
@limiter.limit("60 per hour") # Limit log retrieval
def get_build_log(job_path, build_number_str):
    """
    Gets the console output (log) of a specific build for a job.
    job_path can include folders, e.g., 'MyJob' or 'MyFolder/MyJob'.
    build_number_str should be the build number or 'lastBuild', 'lastSuccessfulBuild', etc.
    """
    if not job_path or not build_number_str:
        logger.warning("Build log request with missing job_path or build_number.")
        return make_error_response("Missing job_path or build_number parameter", 400)

    # Logic to resolve build_number_str (similar to get_build_status)
    @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), reraise=True)
    def _fetch_job_info_for_log(j_path):
        return jenkins_server.get_job_info(j_path)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=4), stop=stop_after_attempt(3), reraise=True)
    def _fetch_console_output(j_path, build_id):
        return jenkins_server.get_build_console_output(j_path, build_id)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=4), stop=stop_after_attempt(3), reraise=True)
    def _fetch_build_info_for_log_url(j_path, build_id): # Similar to one in get_build_status
        return jenkins_server.get_build_info(j_path, build_id)

    def summarize_log_content(log_text: str, max_lines=15) -> str:
        lines = log_text.splitlines()
        summary_parts = []
        error_keywords = ["ERROR", "FAILURE", "Failed", "Traceback (most recent call last):"]
        success_keywords = ["Finished: SUCCESS", "Build successful"]
        
        if not lines:
            return "Log is empty."

        summary_parts.append(f"Log analysis (first {max_lines} lines and key events):")
        
        # Add first few lines
        for i, line in enumerate(lines[:max_lines]):
            summary_parts.append(f"  {line}")
            if i == max_lines -1 and len(lines) > max_lines:
                summary_parts.append("  ...")

        found_errors = []
        found_success = []

        for line_num, line in enumerate(lines):
            for err_key in error_keywords:
                if err_key in line:
                    found_errors.append(f"Error indicator found on line {line_num+1}: {line.strip()}")
            for suc_key in success_keywords:
                if suc_key in line:
                    found_success.append(f"Success indicator found on line {line_num+1}: {line.strip()}")
        
        if found_errors:
            summary_parts.append("\nKey Errors/Failures found:")
            summary_parts.extend([f"  - {err}" for err in found_errors[:5]]) # Limit reported errors
        elif found_success:
            summary_parts.append("\nKey Success indicators found:")
            summary_parts.extend([f"  - {suc}" for suc in found_success])
        else:
            summary_parts.append("\nNo explicit success or error keywords found in the log.")
            
        if "Finished: SUCCESS" in log_text:
            summary_parts.append("\nOverall status: Likely SUCCESSFUL.")
        elif "Finished: FAILURE" in log_text:
            summary_parts.append("\nOverall status: Likely FAILED.")
        elif "Finished: ABORTED" in log_text:
            summary_parts.append("\nOverall status: Likely ABORTED.")
        
        return "\n".join(summary_parts)

    try:
        build_identifier_resolved = None
        if build_number_str.isdigit():
            build_identifier_resolved = int(build_number_str)
        else:
            # Fetch job info to resolve special build strings like 'lastBuild'
            job_info_data = _fetch_job_info_for_log(job_path)
            if build_number_str in job_info_data and \
               isinstance(job_info_data[build_number_str], dict) and \
               'number' in job_info_data[build_number_str]:
                build_identifier_resolved = job_info_data[build_number_str]['number']
            else:
                # Check if it's a direct build reference like 'lastBuild' which might not be in job_info directly
                # but python-jenkins handles some of these if passed as string to get_build_info/console_output
                # However, for console_output, it strictly needs a number.
                # So, we must resolve it to a number first.
                logger.warning(f"Cannot resolve build identifier string '{build_number_str}' for job '{job_path}' to a number for log retrieval.")
                return make_error_response(f"Invalid or unresolvable build identifier string for log: {build_number_str}. Must resolve to a specific build number.", 400)
        
        if build_identifier_resolved is None:
             return make_error_response(f"Could not determine build number for log retrieval: {build_number_str}", 400)

        logger.info(f"Getting console log for job '{job_path}', build #{build_identifier_resolved}")
        
        log_content = _fetch_console_output(job_path, build_identifier_resolved)
        build_info_for_url = _fetch_build_info_for_log_url(job_path, build_identifier_resolved)
        
        log_url = build_info_for_url.get('url', '')
        if log_url and not log_url.endswith('/'):
            log_url += '/'
        log_url += "console" # Standard Jenkins console log URL pattern

        summary = summarize_log_content(log_content)

        return jsonify({
            "job_name": job_path,
            "build_number": build_identifier_resolved,
            "summary": summary,
            "log_url": log_url
        })

    except jenkins.NotFoundException:
        resolved_num_str = str(build_identifier_resolved) if 'build_identifier_resolved' in locals() and build_identifier_resolved is not None else 'N/A'
        logger.warning(f"Job '{job_path}' or build '{build_number_str}' (resolved to {resolved_num_str}) not found for log retrieval.")
        return make_error_response(f"Job '{job_path}' or build '{build_number_str}' not found for log retrieval", 404)
    except RetryError as e:
        logger.error(f"Jenkins API error after retries for job '{job_path}', build '{build_number_str}' log: {e}")
        return make_error_response(f"Jenkins API error after retries: {str(e)}", 500)
    except jenkins.JenkinsException as e:
        logger.error(f"Jenkins API error for job '{job_path}', build '{build_number_str}' log: {e}")
        return make_error_response(f"Jenkins API error: {str(e)}", 500)
    except ValueError: 
        logger.warning(f"Invalid build_number format for log: {build_number_str} for job {job_path}")
        return make_error_response(f"Invalid build_number format for log: {build_number_str}. Must be an integer or resolve to one.", 400)
    except Exception as e:
        logger.error(f"Unexpected error for job '{job_path}', build '{build_number_str}' log: {e}")
        return make_error_response(f"An unexpected error occurred: {str(e)}", 500)


@app.route('/job/<path:job_path>/build', methods=['POST'])
@require_api_key
@limiter.limit("30 per hour") # Example specific limit
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

    raw_payload = request.get_json(silent=True) or {}
    logger.info(f"Triggering build for job: {job_path} with payload: {raw_payload}")

    try:
        # Validate payload using Pydantic
        # The model BuildJobPayload expects a dictionary, potentially with a 'parameters' key.
        # If the payload IS the parameters dict, we wrap it.
        # Jenkins build_job takes parameters as a flat dict.
        
        build_params_dict = {} # This will be the flat dictionary for Jenkins
        if raw_payload: # If payload is not empty
            try:
                # If payload structure is {"parameters": {"key": "value", ...}}
                if "parameters" in raw_payload and isinstance(raw_payload["parameters"], dict):
                    validated_data = BuildJobPayload(parameters=raw_payload["parameters"])
                    build_params_dict = validated_data.parameters if validated_data.parameters else {}
                # If payload structure is {"key": "value", ...} directly
                else:
                    # Wrap it into the expected structure for validation by BuildJobPayload
                    # then extract. This is a bit round-about.
                    # A simpler Pydantic model like `Dict[str, Any]` might be better if the payload is always flat.
                    # For now, let's assume the payload IS the parameters dict.
                    validated_data = BuildJobPayload(parameters=raw_payload) # Treat whole payload as 'parameters'
                    build_params_dict = validated_data.parameters if validated_data.parameters else {}

            except ValidationError as e:
                logger.warning(f"Invalid build parameters for job '{job_path}': {e.errors()}")
                # Provide a more user-friendly error message from Pydantic
                error_details = e.errors()
                # Example: extract first error message
                # user_message = "Invalid input. " + error_details[0]['msg'] if error_details else "Validation failed."
                return make_error_response(f"Invalid build parameters: {error_details}", 400)
        
        logger.info(f"Validated parameters for Jenkins: {build_params_dict}")

        # Check if job exists and is buildable
        @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), reraise=True)
        def _get_job_info_for_build(j_path):
            return jenkins_server.get_job_info(j_path)

        @retry(wait=wait_exponential(multiplier=1, min=2, max=6), stop=stop_after_attempt(3), reraise=True)
        def _trigger_jenkins_build(j_path, params_dict):
            return jenkins_server.build_job(j_path, parameters=params_dict)

        try:
            job_info_data = _get_job_info_for_build(job_path)
            if not job_info_data.get('buildable'):
                logger.warning(f"Attempted to build non-buildable job: {job_path}")
                return make_error_response(f"Job '{job_path}' is not buildable.", 400)
        except jenkins.NotFoundException:
            logger.warning(f"Job '{job_path}' not found for triggering build.")
            return make_error_response(f"Job '{job_path}' not found.", 404)
        # RetryError from _get_job_info_for_build will be caught by the outer try-except

        queue_item_number = _trigger_jenkins_build(job_path, build_params_dict)
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
            "parameters": build_params_dict,
            "queue_item": queue_item_number,
            # "build_number": build_number # if retrieved
        }), 202 # Accepted
    except jenkins.NotFoundException: # Should be caught by specific checks, but as a safeguard
        logger.warning(f"Job '{job_path}' not found when trying to trigger build (outer catch).")
        return make_error_response(f"Job '{job_path}' not found", 404)
    except RetryError as e:
        logger.error(f"Jenkins API error after retries triggering build for job '{job_path}': {e}")
        return make_error_response(f"Jenkins API error after retries: {str(e)}", 500)
    except jenkins.JenkinsException as e:
        logger.error(f"Jenkins API error triggering build for job '{job_path}': {e}")
        return make_error_response(f"Jenkins API error: {str(e)}", 500)
    except Exception as e:
        logger.error(f"Unexpected error triggering build for job '{job_path}': {e}")
        return make_error_response(f"An unexpected error occurred: {str(e)}", 500)


if __name__ == '__main__':
    # For development only. Use a proper WSGI server (e.g., Gunicorn) for production.
    # DEBUG_MODE for Flask app.run's debug is separate from our custom DEBUG_MODE flag.
    # Our DEBUG_MODE controls API key bypass, Flask's debug controls reloader, debugger etc.
    flask_debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    # Use SERVER_PORT from environment for consistency with tests, default to 5000 if not set.
    server_port = int(os.environ.get('SERVER_PORT', '5000'))
    logger.info(f"Starting Flask development server (Flask Debug: {flask_debug_mode}, App DEBUG_MODE: {DEBUG_MODE}) on port {server_port}.")
    app.run(debug=flask_debug_mode, host='0.0.0.0', port=server_port)
