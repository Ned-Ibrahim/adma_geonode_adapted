# Token-Based API Documentation

This document describes the comprehensive token-based APIs for file and folder management in the ADMA GeoNode project.

## Table of Contents
- [Authentication](#authentication)
- [File Operations](#file-operations)
- [Folder Operations](#folder-operations)
- [Error Responses](#error-responses)
- [Python Examples](#python-examples)
- [Management Commands](#management-commands)
- [Security Notes](#security-notes)
- [Deployment Notes](#deployment-notes)

## Authentication

All APIs use token-based authentication. You need to obtain a token first, then include it in the `Authorization` header for all subsequent requests.

### Get Authentication Token

**Endpoint:** `POST /api/v1/auth/token/`

**Description:** Create an authentication token for API access using username and password.

**Request:**
```bash
curl -X POST http://localhost/api/v1/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{
    "username": "your_username",
    "password": "your_password"
  }'
```

**Response:**
```json
{
  "token": "c28ac85af76a695bd998e3d43dc06165841cf22c",
  "user_id": 1,
  "username": "admin",
  "created": false
}
```

### Using the Token

Include the token in the `Authorization` header for all API requests:

```bash
Authorization: Token c28ac85af76a695bd998e3d43dc06165841cf22c
```

**Note:** This uses Token authentication format, not Bearer token format.

## File Operations

### Upload Files

**Endpoint:** `POST /api/v1/files/upload/`

**Description:** Upload one or more files to the system with optional folder assignment.

**Parameters:**
- `files`: List of files to upload (required)
- `folder_id`: UUID of target folder (optional)
- `is_public`: Boolean for public visibility (optional, default: false)

**Request:**
```bash
curl -X POST http://localhost/api/v1/files/upload/ \
  -H "Authorization: Token your_token_here" \
  -F "files=@/path/to/file1.pdf" \
  -F "files=@/path/to/file2.jpg" \
  -F "folder_id=optional-folder-uuid" \
  -F "is_public=false"
```

**Response:**
```json
{
  "success": true,
  "files": [
    {
      "id": "file-uuid-1",
      "name": "file1.pdf",
      "file_type": "document",
      "size": "1.2 MB",
      "is_spatial": false,
      "is_public": false,
      "url": "/file/file-uuid-1/",
      "download_url": "/api/v1/files/file-uuid-1/download/"
    }
  ],
  "message": "Successfully uploaded 2 files"
}
```

### Download File

**Endpoint:** `GET /api/v1/files/{file_id}/download/`

**Description:** Download a specific file by its UUID.

**Request:**
```bash
curl -X GET http://localhost/api/v1/files/file-uuid-here/download/ \
  -H "Authorization: Token your_token_here" \
  -o downloaded_file.pdf
```

**Response:** File content with appropriate headers

### List Files

**Endpoint:** `GET /api/v1/files/`

**Description:** List user's files with optional filtering.

**Query Parameters:**
- `folder_id`: Filter by folder UUID (optional)
- `is_public`: Filter by public status (`true`/`false`) (optional)
- `file_type`: Filter by file type (`document`, `image`, `spatial`, etc.) (optional)

**Request:**
```bash
curl -X GET "http://localhost/api/v1/files/?folder_id=folder-uuid&is_public=true" \
  -H "Authorization: Token your_token_here"
```

**Response:**
```json
{
  "files": [
    {
      "id": "file-uuid",
      "name": "example.pdf",
      "file_type": "document",
      "file_size": 1234567,
      "is_public": true,
      "is_spatial": false,
      "gis_status": null,
      "created_at": "2023-01-01T00:00:00Z",
      "updated_at": "2023-01-01T00:00:00Z"
    }
  ],
  "count": 1
}
```

## Folder Operations

### Upload Folder Structure

**Endpoint:** `POST /api/v1/folders/upload/`

**Description:** Upload files with folder structure preservation.

**Parameters:**
- `files`: List of files to upload (required)
- `file_paths`: List of corresponding folder paths (required)
- `folder_id`: UUID of parent folder (optional)
- `is_public`: Boolean for public visibility (optional, default: false)

**Request:**
```bash
curl -X POST http://localhost/api/v1/folders/upload/ \
  -H "Authorization: Token your_token_here" \
  -F "files=@/path/to/folder1/file1.txt" \
  -F "files=@/path/to/folder1/subfolder/file2.txt" \
  -F "file_paths=folder1/file1.txt" \
  -F "file_paths=folder1/subfolder/file2.txt" \
  -F "folder_id=optional-parent-folder-uuid" \
  -F "is_public=false"
```

**Response:**
```json
{
  "success": true,
  "folders_created": 2,
  "files_uploaded": 2,
  "message": "Successfully uploaded folder structure: 2 folders, 2 files"
}
```

### List Folders

**Endpoint:** `GET /api/v1/folders/`

**Description:** List user's folders with optional filtering.

**Query Parameters:**
- `parent_id`: Filter by parent folder UUID (optional)
- `is_public`: Filter by public status (`true`/`false`) (optional)

**Request:**
```bash
curl -X GET "http://localhost/api/v1/folders/?parent_id=folder-uuid" \
  -H "Authorization: Token your_token_here"
```

**Response:**
```json
{
  "folders": [
    {
      "id": "folder-uuid",
      "name": "My Folder",
      "is_public": false,
      "created_at": "2023-01-01T00:00:00Z",
      "updated_at": "2023-01-01T00:00:00Z"
    }
  ],
  "count": 1
}
```

### Get Folder Information

**Endpoint:** `GET /api/v1/folders/{folder_id}/info/`

**Description:** Get detailed folder information including file count and total size before downloading.

**Request:**
```bash
curl -X GET http://localhost/api/v1/folders/folder-uuid-here/info/ \
  -H "Authorization: Token your_token_here"
```

**Response:**
```json
{
  "folder_id": "folder-uuid",
  "folder_name": "My Documents",
  "zip_filename": "my-documents.zip",
  "file_count": 15,
  "total_size": 2048576,
  "total_size_display": "2.0 MB",
  "is_public": false,
  "created_at": "2023-01-01T00:00:00Z",
  "download_url": "/api/v1/folders/folder-uuid/download/"
}
```

### Download Folder as ZIP

**Endpoint:** `GET /api/v1/folders/{folder_id}/download/`

**Description:** Download entire folder structure as a compressed ZIP file.

**Query Parameters:**
- `include_subfolders`: Include subfolders recursively (`true`/`false`, default: `true`)

**Request:**
```bash
curl -X GET "http://localhost/api/v1/folders/folder-uuid-here/download/?include_subfolders=true" \
  -H "Authorization: Token your_token_here" \
  -o my_folder.zip
```

**Response:** ZIP file containing all files in the folder

**Response Headers:**
- `Content-Type: application/zip`
- `Content-Disposition: attachment; filename="folder-name.zip"`
- `X-Folder-Name`: Original folder name
- `X-File-Count`: Number of files in the ZIP
- `X-Total-Size`: Total size of all files in bytes

## Error Responses

All APIs return consistent error responses with appropriate HTTP status codes:

```json
{
  "error": "Error message describing what went wrong"
}
```

### Common HTTP Status Codes:
- `200`: Success
- `201`: Created successfully
- `400`: Bad request (invalid data)
- `401`: Unauthorized (invalid/missing token)
- `404`: Not found
- `500`: Internal server error

### Common Error Scenarios:

**Invalid Token:**
```json
{
  "detail": "Invalid token."
}
```

**File Not Found:**
```json
{
  "error": "File not found or access denied"
}
```

**Upload Validation Error:**
```json
{
  "files": ["This field is required."]
}
```

## Python Examples

### Complete Python Workflow

```python
import requests
import os

# Configuration
BASE_URL = "http://localhost/api/v1"
USERNAME = "admin"
PASSWORD = "admin123"

# 1. Get authentication token
auth_response = requests.post(f"{BASE_URL}/auth/token/", json={
    "username": USERNAME,
    "password": PASSWORD
})

if auth_response.status_code == 200:
    token = auth_response.json()["token"]
    print(f"Token obtained: {token}")
else:
    print(f"Authentication failed: {auth_response.json()}")
    exit(1)

# 2. Set headers for subsequent requests
headers = {"Authorization": f"Token {token}"}

# 3. Upload single files
print("\n=== Uploading Files ===")
with open("example.pdf", "rb") as f:
    files = {"files": f}
    data = {"is_public": "true"}
    
    upload_response = requests.post(
        f"{BASE_URL}/files/upload/",
        headers=headers,
        files=files,
        data=data
    )
    
    if upload_response.status_code == 201:
        uploaded_file = upload_response.json()["files"][0]
        file_id = uploaded_file["id"]
        print(f"File uploaded: {uploaded_file['name']} (ID: {file_id})")
    else:
        print(f"Upload failed: {upload_response.json()}")

# 4. Upload folder structure
print("\n=== Uploading Folder Structure ===")
folder_files = [
    ("files", open("docs/file1.txt", "rb")),
    ("files", open("docs/subfolder/file2.txt", "rb"))
]
folder_data = {
    "file_paths": ["docs/file1.txt", "docs/subfolder/file2.txt"],
    "is_public": "false"
}

folder_upload_response = requests.post(
    f"{BASE_URL}/folders/upload/",
    headers=headers,
    files=folder_files,
    data=folder_data
)

# Close file handles
for _, file_handle in folder_files:
    file_handle.close()

if folder_upload_response.status_code == 201:
    result = folder_upload_response.json()
    print(f"Folder structure uploaded: {result['folders_created']} folders, {result['files_uploaded']} files")

# 5. List files
print("\n=== Listing Files ===")
list_response = requests.get(f"{BASE_URL}/files/", headers=headers)
if list_response.status_code == 200:
    files = list_response.json()["files"]
    print(f"Found {len(files)} files")
    for file in files:
        print(f"  - {file['name']} ({file['file_type']}, {file.get('size', 'unknown size')})")

# 6. Download file
print("\n=== Downloading File ===")
if 'file_id' in locals():
    download_response = requests.get(
        f"{BASE_URL}/files/{file_id}/download/",
        headers=headers
    )
    
    if download_response.status_code == 200:
        with open("downloaded_file.pdf", "wb") as f:
            f.write(download_response.content)
        print("File downloaded successfully")

# 7. Get folder info and download as ZIP
print("\n=== Folder Operations ===")
folders_response = requests.get(f"{BASE_URL}/folders/", headers=headers)
if folders_response.status_code == 200 and folders_response.json()["folders"]:
    folder_id = folders_response.json()["folders"][0]["id"]
    
    # Get folder information
    folder_info_response = requests.get(
        f"{BASE_URL}/folders/{folder_id}/info/",
        headers=headers
    )
    
    if folder_info_response.status_code == 200:
        folder_info = folder_info_response.json()
        print(f"Folder: {folder_info['folder_name']}")
        print(f"Files: {folder_info['file_count']}")
        print(f"Size: {folder_info['total_size_display']}")
        
        # Download folder as ZIP
        folder_download_response = requests.get(
            f"{BASE_URL}/folders/{folder_id}/download/",
            headers=headers
        )
        
        if folder_download_response.status_code == 200:
            with open(f"{folder_info['zip_filename']}", "wb") as f:
                f.write(folder_download_response.content)
            print(f"Folder downloaded as: {folder_info['zip_filename']}")

print("\n=== API Demo Complete ===")
```

### Error Handling Example

```python
import requests

def api_request_with_error_handling(method, url, headers, **kwargs):
    """Make API request with comprehensive error handling"""
    try:
        response = requests.request(method, url, headers=headers, **kwargs)
        
        if response.status_code == 200:
            return response.json(), None
        elif response.status_code == 201:
            return response.json(), None
        elif response.status_code == 401:
            return None, "Authentication failed - check your token"
        elif response.status_code == 404:
            return None, "Resource not found"
        elif response.status_code == 400:
            error_detail = response.json().get('error', 'Bad request')
            return None, f"Validation error: {error_detail}"
        else:
            return None, f"API error {response.status_code}: {response.text}"
            
    except requests.exceptions.ConnectionError:
        return None, "Connection failed - check if server is running"
    except requests.exceptions.Timeout:
        return None, "Request timeout"
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"

# Usage example
headers = {"Authorization": "Token your_token_here"}
data, error = api_request_with_error_handling(
    "GET", 
    "http://localhost/api/v1/files/", 
    headers
)

if error:
    print(f"Error: {error}")
else:
    print(f"Success: {data}")
```

## Management Commands

### Create API Token

You can create tokens using Django management commands:

```bash
# Create token for a user
docker-compose exec django python manage.py create_api_token username

# Regenerate existing token
docker-compose exec django python manage.py create_api_token username --regenerate
```

**Example:**
```bash
$ docker-compose exec django python manage.py create_api_token admin
Created token for user "admin": c28ac85af76a695bd998e3d43dc06165841cf22c

$ docker-compose exec django python manage.py create_api_token admin --regenerate
Regenerated token for user "admin": d39bc96af87b706ce109f4e54ed07276952df33d
```

## Security Notes

1. **Keep tokens secure**: Treat API tokens like passwords
2. **Use HTTPS**: Always use HTTPS in production to protect tokens in transit
3. **Token rotation**: Regenerate tokens periodically for security
4. **Access control**: Tokens have the same permissions as the user account
5. **Rate limiting**: APIs may be rate-limited to prevent abuse
6. **File permissions**: Users can only access their own files unless marked as public
7. **Input validation**: All file uploads are validated for type and size
8. **Path traversal protection**: File paths are sanitized to prevent directory traversal attacks

## Deployment Notes

### Production Deployment

For production deployment, ensure the following:

1. **Update Base URL**: Change `http://localhost` to your production domain
2. **Enable HTTPS**: Configure SSL/TLS certificates
3. **Configure Rate Limiting**: Set up Nginx rate limiting for API endpoints
4. **Set up Monitoring**: Monitor API usage and error rates
5. **Environment Variables**: Use environment variables for sensitive configuration
6. **Database Backups**: Regular backups of user data and tokens
7. **Log Management**: Configure proper logging for API requests and errors

### Environment Configuration

```bash
# Production environment variables
DJANGO_SETTINGS_MODULE=adma_geo.settings
DEBUG=False
ALLOWED_HOSTS=your-domain.com
CSRF_TRUSTED_ORIGINS=https://your-domain.com
USE_HTTPS=True
```

### Nginx Rate Limiting Example

```nginx
# Rate limiting for API endpoints
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

location /api/v1/ {
    limit_req zone=api burst=20 nodelay;
    proxy_pass http://django;
    # ... other proxy settings
}
```

## API Endpoints Summary

| **Method** | **Endpoint** | **Description** |
|------------|--------------|-----------------|
| `POST` | `/api/v1/auth/token/` | Create authentication token |
| `POST` | `/api/v1/files/upload/` | Upload files |
| `GET` | `/api/v1/files/` | List files |
| `GET` | `/api/v1/files/{id}/download/` | Download file |
| `POST` | `/api/v1/folders/upload/` | Upload folder structure |
| `GET` | `/api/v1/folders/` | List folders |
| `GET` | `/api/v1/folders/{id}/info/` | Get folder information |
| `GET` | `/api/v1/folders/{id}/download/` | Download folder as ZIP |

---

**Last Updated:** November 2025  
**API Version:** v1  
**Documentation Version:** 2.0