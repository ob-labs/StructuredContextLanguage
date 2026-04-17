#!/bin/bash
# Test script: send REST requests and create task files in JSON/YAML format
# Validates Task class structure (system_prompt, prompt_list, capacity, status, etc.)
# Tests both valid and invalid payloads against scl.meta.task.Task expectations

set -e

# Configuration
HOST="localhost"
PORT="8080"
TODO_DIR="${TODO_WATCH_DIR:-./todo_folder}"
PROCESSED_DIR="$TODO_DIR/processed"
FAILED_DIR="$TODO_DIR/failed"
REST_URL="http://$HOST:$PORT/todo"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=== Testing Todo Receiver (Task format) ==="

# Check if service is running
if ! curl -s "http://$HOST:$PORT/docs" > /dev/null; then
    echo -e "${RED}Error: Service not running. Start main.py first.${NC}"
    exit 1
fi

# Ensure watch directory exists
mkdir -p "$TODO_DIR"

# Helper: Count files in a directory (if exists)
count_files() {
    if [ -d "$1" ]; then
        find "$1" -maxdepth 1 -type f | wc -l
    else
        echo "0"
    fi
}

# Helper: Wait for file movement (up to 5 seconds)
wait_for_file_moved() {
    local original="$1"
    local target_dir="$2"
    local before_count=$(count_files "$target_dir")
    local waited=0
    while [ $waited -lt 5 ]; do
        if [ ! -f "$original" ] && [ $(count_files "$target_dir") -gt $before_count ]; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 1
}

echo -e "\n${YELLOW}1. Testing REST API with VALID Task (minimal)${NC}"
VALID_TASK_JSON='{"system_prompt": "You are a test assistant."}'
echo "Payload: $VALID_TASK_JSON"
RESPONSE=$(curl -s -X POST "$REST_URL" -H "Content-Type: application/json" -d "$VALID_TASK_JSON")
echo "Response: $RESPONSE"
if [[ "$RESPONSE" == *"accepted"* ]]; then
    echo -e "${GREEN}✓ Valid minimal Task accepted${NC}"
else
    echo -e "${RED}✗ Expected acceptance but got: $RESPONSE${NC}"
fi

echo -e "\n${YELLOW}2. Testing REST API with FULL Task structure${NC}"
FULL_TASK_JSON=$(cat <<EOF
{
  "system_prompt": "Full task system prompt",
  "prompt_list": ["First user message", "Assistant response"],
  "capacity": ["cpu", "memory", "storage"],
  "status": "created",
  "additional": {
    "priority": "high",
    "project": "test"
  },
  "previous_hash": null,
  "sub_tasks": []
}
EOF
)
echo "Payload: $FULL_TASK_JSON"
RESPONSE=$(curl -s -X POST "$REST_URL" -H "Content-Type: application/json" -d "$FULL_TASK_JSON")
echo "Response: $RESPONSE"
if [[ "$RESPONSE" == *"accepted"* ]]; then
    echo -e "${GREEN}✓ Full Task accepted${NC}"
else
    echo -e "${RED}✗ Expected acceptance but got: $RESPONSE${NC}"
fi

echo -e "\n${YELLOW}3. Testing REST API with INVALID JSON syntax${NC}"
INVALID_JSON='{"system_prompt": "missing closing brace"'
echo "Payload: $INVALID_JSON"
RESPONSE=$(curl -s -X POST "$REST_URL" -H "Content-Type: application/json" -d "$INVALID_JSON")
echo "Response: $RESPONSE"
if [[ "$RESPONSE" == *"Invalid JSON body"* ]] || [[ "$RESPONSE" == *"422"* ]]; then
    echo -e "${GREEN}✓ Invalid JSON correctly rejected${NC}"
else
    echo -e "${RED}✗ Expected rejection but got: $RESPONSE${NC}"
fi

echo -e "\n${YELLOW}4. Testing REST API with MISSING required field (system_prompt)${NC}"
MISSING_FIELD_JSON='{"prompt_list": ["only prompt"]}'
echo "Payload: $MISSING_FIELD_JSON"
RESPONSE=$(curl -s -X POST "$REST_URL" -H "Content-Type: application/json" -d "$MISSING_FIELD_JSON")
echo "Response: $RESPONSE"
if [[ "$RESPONSE" == *"Invalid task format"* ]] || [[ "$RESPONSE" == *"422"* ]]; then
    echo -e "${GREEN}✓ Missing system_prompt correctly rejected${NC}"
else
    echo -e "${RED}✗ Expected rejection but got: $RESPONSE${NC}"
fi

echo -e "\n${YELLOW}5. Testing REST API with INVALID status value${NC}"
INVALID_STATUS_JSON='{"system_prompt": "test", "status": "unknown_status"}'
echo "Payload: $INVALID_STATUS_JSON"
RESPONSE=$(curl -s -X POST "$REST_URL" -H "Content-Type: application/json" -d "$INVALID_STATUS_JSON")
echo "Response: $RESPONSE"
if [[ "$RESPONSE" == *"Invalid task format"* ]] || [[ "$RESPONSE" == *"422"* ]]; then
    echo -e "${GREEN}✓ Invalid status correctly rejected${NC}"
else
    echo -e "${RED}✗ Expected rejection but got: $RESPONSE${NC}"
fi

echo -e "\n${YELLOW}6. Testing FILE WATCHER with VALID JSON Task${NC}"
VALID_JSON_FILE="$TODO_DIR/valid_task_$(date +%s).json"
cat > "$VALID_JSON_FILE" <<EOF
{
  "system_prompt": "File-based task system prompt",
  "prompt_list": ["File prompt 1", "File prompt 2"],
  "capacity": ["file_cap"],
  "status": "created"
}
EOF
echo "Created: $VALID_JSON_FILE"
if wait_for_file_moved "$VALID_JSON_FILE" "$PROCESSED_DIR"; then
    echo -e "${GREEN}✓ Valid JSON file moved to processed${NC}"
else
    echo -e "${RED}✗ File not moved to processed in time${NC}"
fi

echo -e "\n${YELLOW}7. Testing FILE WATCHER with VALID YAML Task${NC}"
VALID_YAML_FILE="$TODO_DIR/valid_task_$(date +%s).yaml"
cat > "$VALID_YAML_FILE" <<EOF
system_prompt: "YAML task system prompt"
prompt_list:
  - "YAML prompt 1"
  - "YAML prompt 2"
capacity:
  - yaml_cap
status: subtasking
additional:
  source: yaml_file
EOF
echo "Created: $VALID_YAML_FILE"
if wait_for_file_moved "$VALID_YAML_FILE" "$PROCESSED_DIR"; then
    echo -e "${GREEN}✓ Valid YAML file moved to processed${NC}"
else
    echo -e "${RED}✗ File not moved to processed in time${NC}"
fi

echo -e "\n${YELLOW}8. Testing FILE WATCHER with INVALID extension (.txt)${NC}"
INVALID_EXT_FILE="$TODO_DIR/bad_extension_$(date +%s).txt"
echo '{"system_prompt": "should fail"}' > "$INVALID_EXT_FILE"
echo "Created: $INVALID_EXT_FILE"
if wait_for_file_moved "$INVALID_EXT_FILE" "$FAILED_DIR"; then
    echo -e "${GREEN}✓ Invalid extension file moved to failed${NC}"
else
    echo -e "${RED}✗ File not moved to failed in time${NC}"
fi

echo -e "\n${YELLOW}9. Testing FILE WATCHER with INVALID JSON syntax${NC}"
INVALID_JSON_FILE="$TODO_DIR/invalid_json_$(date +%s).json"
echo '{system_prompt: missing quotes}' > "$INVALID_JSON_FILE"
echo "Created: $INVALID_JSON_FILE"
if wait_for_file_moved "$INVALID_JSON_FILE" "$FAILED_DIR"; then
    echo -e "${GREEN}✓ Invalid JSON syntax moved to failed${NC}"
else
    echo -e "${RED}✗ File not moved to failed in time${NC}"
fi

echo -e "\n${YELLOW}10. Testing FILE WATCHER with MISSING system_prompt in JSON${NC}"
MISSING_FIELD_FILE="$TODO_DIR/missing_field_$(date +%s).json"
echo '{"prompt_list": ["no system prompt"]}' > "$MISSING_FIELD_FILE"
echo "Created: $MISSING_FIELD_FILE"
if wait_for_file_moved "$MISSING_FIELD_FILE" "$FAILED_DIR"; then
    echo -e "${GREEN}✓ Missing system_prompt moved to failed${NC}"
else
    echo -e "${RED}✗ File not moved to failed in time${NC}"
fi

echo -e "\n${YELLOW}Internal generation (every 30s) - check logs${NC}"

echo -e "\n${GREEN}=== Test Complete ===${NC}"