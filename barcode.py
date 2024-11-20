from flask import Flask, request, render_template, redirect, url_for, jsonify, session
import jsonref
import requests
from flask_session import Session
from collections import defaultdict
from flask_caching import Cache
from flask_cors import CORS
from urllib.parse import urlencode
from dotenv import load_dotenv
import os
import wmt

app = Flask(__name__)
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 3600})  # Cache timeout set to 1 hour
CORS(app)  # Enable CORS for all routes

# Configure Secret Key and Session Type
app.config['SECRET_KEY'] =  os.urandom(24)  # Replace with a strong secret key
app.config['SESSION_TYPE'] = 'filesystem'  # Server-side sessions
Session(app)

load_dotenv()

# Global variables
minimal_api_spec = {}
grouped_endpoints = defaultdict(list)

# Load environment variables
BARCODE_BUDDY_URL = os.getenv('BARCODE_BUDDY_URL')
BARCODE_BUDDY_KEY = os.getenv('BARCODE_BUDDY_KEY')
GROCY_API_BASE_URL = os.getenv('GROCY_API_BASE_URL')
GROCY_API_KEY = os.getenv('GROCY_API_KEY')





def extract_endpoint_info(api_spec):
    minimal_spec = {}
    grouped_end = defaultdict(list)

    if not api_spec or "paths" not in api_spec:
        return minimal_spec, grouped_end

    for path, methods_dict in api_spec["paths"].items():
        minimal_spec[path] = {}
        for method, details in methods_dict.items():
            method_lower = method.lower()
            minimal_spec[path][method_lower] = {
                "summary": details.get("summary", ""),
                "tags": details.get("tags", ['Miscellaneous']),
                "parameters": [
                    {
                        "name": param.get("name"),
                        "required": param.get("required", False),
                        "schema": {
                            "type": param.get("schema", {}).get("type", "string"),
                            "enum": param.get("schema", {}).get("enum", None)
                        },
                        "description": param.get("description", "")
                    }
                    for param in details.get("parameters", [])
                ],
            }
            # Build grouped_endpoints here
            tags = details.get('tags', ['Miscellaneous'])
            summary = details.get('summary', '')
            category = tags[0]
            grouped_end[category].append({
                'method': method.upper(),
                'path': path,
                'summary': summary
            })

    return minimal_spec, grouped_end

@cache.cached(timeout=500, key_prefix='minimal_api_spec')
def get_minimal_api_spec():
    try:
        with open('static/grocy.openapi.json', 'r') as f:
            api_spec = jsonref.load(f)
            minimal_spec, grouped_end = extract_endpoint_info(api_spec)
            return minimal_spec, grouped_end
    except Exception as e:
        print(f"Error loading OpenAPI specification: {e}")
        return {}, {}

@app.route('/fetch_parameters', methods=['POST'])
def fetch_parameters():
    data = request.form
    method_endpoint = data.get('endpoint')
    if not method_endpoint:
        return redirect(url_for('index'))

    method, endpoint = method_endpoint.split(' ', 1)

    # Build query parameters
    query_params = {
        'method': method.upper(),
        'endpoint': endpoint
    }
    query_string = urlencode(query_params)

    # Redirect to the index route with query parameters
    return redirect(f"/?{query_string}")

@app.route('/execute_api_call', methods=['POST'])
def execute_api_call_route():
    data = request.form.to_dict()
    method = data.pop('method', '').upper()
    endpoint = data.pop('endpoint', '')

    # Trim unused values from data
    if endpoint in minimal_api_spec and method.lower() in minimal_api_spec[endpoint]:
        valid_params = {param['name'] for param in minimal_api_spec[endpoint][method.lower()]['parameters']}
        data = {k: v for k, v in data.items() if k in valid_params and v}

    try:
        result = execute_api_call(method, endpoint, data)

        # Construct the full URL
        full_url = GROCY_API_BASE_URL + endpoint
        if data:  # Add query parameters if any
            full_url += '?' + '&'.join([f"{k}={v}" for k, v in data.items()])

        response_data = {
            "full_url": full_url,
            "api_result": result
        }

        # Store the latest run in the session
        session['latest_run'] = { 
            "minimal_spec": minimal_api_spec,
            "grouped_end": grouped_endpoints,
            "endpoint": endpoint,
            "method": method,
            "parameters": [],  # No need to display parameters again
            "result": response_data,
            "error": None
        }

        # Redirect to the index route
        return redirect(url_for('index'))
    except Exception as e:
        # Store the error in the session
        session['latest_run'] = { 
            "minimal_spec": minimal_api_spec,
            "grouped_end": grouped_endpoints,
            "endpoint": endpoint,
            "method": method,
            "parameters": [],  # No parameters to display again
            "result": None,
            "error": str(e)
        }
        # Redirect to the index route
        return redirect(url_for('index'))

@app.route('/send_scan', methods=['POST'])
def send_scan():
    try:
        if request.form.get('state'):  # Handle button press
            state = request.form.get('state')  # Check if a button was pressed
            # Prepare the data for the POST request
            url = BARCODE_BUDDY_URL + "/state/setmode"
            headers = {"BBUDDY-API-KEY": BARCODE_BUDDY_KEY}
            data = {"state": state}

            # Make the POST request
            response = requests.post(url, headers=headers, params=data)
            print(data)
            if response.ok:
                latest_result = f"State set to {state} successfully."
            else:
                raise Exception(f"Failed to set state: {response.text}")

        else:  # Handle barcode scanning
            barcode = request.form.get('barcode')
            # Prepare the data for the barcode POST request
            url = BARCODE_BUDDY_URL + "/action/scan"
            headers = {"BBUDDY-API-KEY": BARCODE_BUDDY_KEY}
            data = {"text": barcode}
            print(data)
            # Make the POST request
            response = requests.post(url, headers=headers, params=data)

            if response.ok:
                latest_result = "Barcode scanned successfully."
            else:
                raise Exception(f"Failed to scan barcode: {response.text}")

        # Store the latest result in the session
        session['latest_run'] = {
            "minimal_spec": minimal_api_spec,
            "grouped_end": grouped_endpoints,
            "endpoint": "/send_scan",
            "method": "POST",
            "parameters": [],
            "result": {"result": latest_result},
            "error": None,
        }

        return redirect(url_for('index'))

    except Exception as e:
        # Store the error in the session
        session['latest_run'] = {
            "minimal_spec": minimal_api_spec,
            "grouped_end": grouped_endpoints,
            "endpoint": "/send_scan",
            "method": "POST",
            "parameters": [],
            "result": None,
            "error": str(e),
        }
        return redirect(url_for('index'))


@app.route('/', methods=['GET'])
def index():
    minimal_api_spec, grouped_endpoints = get_minimal_api_spec()

    method = request.args.get('method')
    endpoint = request.args.get('endpoint')
    parameters = []
    result = None
    error = None

    # If method is not provided, try to extract it from endpoint
    if not method and endpoint:
        if ' ' in endpoint:
            method, endpoint = endpoint.split(' ', 1)
        else:
            method = 'GET'  # Default to GET if method is not provided

    # If method and endpoint are provided, get the parameters
    if method and endpoint:
        method_lower = method.lower()
        if endpoint in minimal_api_spec and method_lower in minimal_api_spec[endpoint]:
            endpoint_details = minimal_api_spec[endpoint][method_lower]
            parameters = endpoint_details.get('parameters', [])

    # Retrieve the latest run from the session
    latest_run = session.pop('latest_run', None)
    if latest_run:
        result = latest_run.get('result')
        error = latest_run.get('error')

    # Render the template
    return render_template(
        'index.html',
        api_spec=minimal_api_spec,
        method=method,
        grouped_end=grouped_endpoints,
        endpoint=endpoint,
        parameters=parameters,
        result=result,
        error=error
    )

# Execute API call function
def execute_api_call(method, endpoint, params):
    # Replace path parameters in the endpoint URL
    for param_name, param_value in list(params.items()):
        placeholder = f"{{{param_name}}}"
        if placeholder in endpoint:
            endpoint = endpoint.replace(placeholder, str(param_value))
            del params[param_name]  # Remove path parameters from params

    params = {k: v for k, v in params.items() if v not in (None, '', [])}

    url = GROCY_API_BASE_URL + endpoint
    headers = {"GROCY-API-KEY": GROCY_API_KEY}
    response = None
    print(GROCY_API_BASE_URL + endpoint + " " + method)
    method_upper = method.upper()

    if method_upper == 'GET':
        response = requests.get(url, headers=headers, params=params)
    elif method_upper == 'POST':
        response = requests.post(url, headers=headers, json=params)
    elif method_upper == 'PUT':
        response = requests.put(url, headers=headers, json=params)
    elif method_upper == 'DELETE':
        response = requests.delete(url, headers=headers, params=params)
    else:
        raise Exception(f"Unsupported HTTP method: {method}")

    #response.raise_for_status()
    return response.json()



if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)
