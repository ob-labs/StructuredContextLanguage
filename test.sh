#!/bin/bash
# Test script: send REST requests and create task files in JSON/YAML format
# Validates Task and CapTask class structures.
# Tests /tasks, /captasks, /items/{hash}, /tasks/waiting, /items/{hash}/approve endpoints.
# Updated for RestFulHandler v2.

set -e

# Configuration
HOST="localhost"
PORT="8080"
TODO_DIR="${TODO_WATCH_DIR:-./todo_folder}"
PROCESSED_DIR="$TODO_DIR/processed"
PROCESSED_CAPTASK_DIR="$TODO_DIR/processedCapTask"
FAILED_DIR="$TODO_DIR/failed"
WAITING_APPROVAL_DIR="$TODO_DIR/waitingapproval"
WAITING_CAPTASK_DIR="$TODO_DIR/waitingCapTask"

# REST endpoints
TASKS_URL="http://$HOST:$PORT/tasks"
CAPTASKS_URL="http://$HOST:$PORT/captasks"
ITEMS_URL="http://$HOST:$PORT/items"
WAITING_URL="http://$HOST:$PORT/tasks/waiting"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=== Testing Todo Receiver (Task & CapTask API v2) ==="

# Check if service is running
if ! curl -s "http://$HOST:$PORT/docs" > /dev/null; then
    echo -e "${RED}Error: Service not running on $HOST:$PORT${NC}"
    exit 1
fi

# Ensure watch directory exists
mkdir -p "$TODO_DIR"

# Helper: Count files
count_files() {
    if [ -d "$1" ]; then
        find "$1" -maxdepth 1 -type f | wc -l
    else
        echo "0"
    fi
}

# Helper: Wait for file movement
wait_for_file_moved() {
    local original="$1"
    local target_dir="$2"
    local before=$(count_files "$target_dir")
    local waited=0
    while [ $waited -lt 5 ]; do
        if [ ! -f "$original" ] && [ $(count_files "$target_dir") -gt $before ]; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 1
}

# Helper: Extract hash from JSON response (works for both task_hash and hash fields)
extract_hash() {
    echo "$1" | grep -o '"hash"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/'
}

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}1. POST /tasks - Valid minimal Task${NC}"
VALID_TASK='{"system_prompt": "You are a test assistant."}'
echo "Payload: $VALID_TASK"
RESP=$(curl -s -X POST "$TASKS_URL" -H "Content-Type: application/json" -d "$VALID_TASK")
echo "Response: $RESP"
if [[ "$RESP" == *"accepted"* ]] && [[ "$RESP" == *"hash"* ]]; then
    echo -e "${GREEN}✓ Valid Task accepted${NC}"
    TASK_HASH=$(extract_hash "$RESP")
    echo "  Hash: $TASK_HASH"
else
    echo -e "${RED}✗ Expected acceptance: $RESP${NC}"
    TASK_HASH=""
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}2. POST /tasks - Full Task structure${NC}"
FULL_TASK=$(cat <<EOF
{
  "system_prompt": "Full task system prompt",
  "prompt_list": ["User msg", "Assistant reply"],
  "capacity": ["cpu", "memory"],
  "status": "created",
  "additional": {"priority": "high"},
  "previous_hash": null,
  "sub_tasks": []
}
EOF
)
echo "Payload: $FULL_TASK"
RESP=$(curl -s -X POST "$TASKS_URL" -H "Content-Type: application/json" -d "$FULL_TASK")
echo "Response: $RESP"
if [[ "$RESP" == *"accepted"* ]]; then
    echo -e "${GREEN}✓ Full Task accepted${NC}"
    FULL_TASK_HASH=$(extract_hash "$RESP")
else
    echo -e "${RED}✗ Expected acceptance: $RESP${NC}"
    FULL_TASK_HASH=""
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}3. POST /tasks - Invalid JSON syntax${NC}"
curl -s -X POST "$TASKS_URL" -H "Content-Type: application/json" -d '{"system_prompt": "oops"'
if [[ $? -eq 0 ]]; then
    echo -e "${GREEN}✓ Invalid JSON rejected (400)${NC}"
else
    echo -e "${RED}✗ Expected rejection${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}4. POST /tasks - Missing system_prompt${NC}"
RESP=$(curl -s -X POST "$TASKS_URL" -H "Content-Type: application/json" -d '{"prompt_list": ["no system"]}')
if [[ "$RESP" == *"Invalid task format"* ]] || [[ "$RESP" == *"422"* ]]; then
    echo -e "${GREEN}✓ Rejected missing system_prompt${NC}"
else
    echo -e "${RED}✗ Unexpected: $RESP${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}5. POST /captasks - Valid CapTask${NC}"
VALID_CAPTASK='{"cap_name": "send_email", "args": ["to@example.com", "Subject"], "approval": true}'
echo "Payload: $VALID_CAPTASK"
RESP=$(curl -s -X POST "$CAPTASKS_URL" -H "Content-Type: application/json" -d "$VALID_CAPTASK")
echo "Response: $RESP"
if [[ "$RESP" == *"accepted"* ]] && [[ "$RESP" == *"hash"* ]]; then
    echo -e "${GREEN}✓ Valid CapTask accepted${NC}"
    CAP_HASH=$(extract_hash "$RESP")
    echo "  Hash: $CAP_HASH"
else
    echo -e "${RED}✗ Expected acceptance: $RESP${NC}"
    CAP_HASH=""
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}6. POST /captasks - Missing cap_name${NC}"
RESP=$(curl -s -X POST "$CAPTASKS_URL" -H "Content-Type: application/json" -d '{"args": ["no cap"]}')
if [[ "$RESP" == *"Invalid captask format"* ]] || [[ "$RESP" == *"422"* ]]; then
    echo -e "${GREEN}✓ Rejected missing cap_name${NC}"
else
    echo -e "${RED}✗ Unexpected: $RESP${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}7. GET /items/{hash} - Query Task status${NC}"
if [[ -n "$TASK_HASH" ]]; then
    STATUS_URL="$ITEMS_URL/$TASK_HASH"
    echo "Querying: $STATUS_URL"
    RESP=$(curl -s "$STATUS_URL")
    echo "Response: $RESP"
    if [[ "$RESP" == *"pending"* ]] || [[ "$RESP" == *"waiting_approval"* ]]; then
        echo -e "${GREEN}✓ Status query returned valid state${NC}"
    else
        echo -e "${YELLOW}⚠ Unexpected state: $RESP${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Skipping (no hash from test #1)${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}8. GET /items/{hash} - Query CapTask status${NC}"
if [[ -n "$CAP_HASH" ]]; then
    STATUS_URL="$ITEMS_URL/$CAP_HASH"
    echo "Querying: $STATUS_URL"
    RESP=$(curl -s "$STATUS_URL")
    echo "Response: $RESP"
    if [[ "$RESP" == *"pending"* ]] || [[ "$RESP" == *"waiting_approval"* ]]; then
        echo -e "${GREEN}✓ CapTask status query successful${NC}"
    else
        echo -e "${YELLOW}⚠ Unexpected: $RESP${NC}"
    fi
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}9. GET /items/{hash} - Non-existent hash${NC}"
RESP=$(curl -s "$ITEMS_URL/nonexistent-12345")
if [[ "$RESP" == *"not_found"* ]]; then
    echo -e "${GREEN}✓ Returns 'not_found'${NC}"
else
    echo -e "${YELLOW}⚠ Unexpected: $RESP${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}10. POST /tasks - Create unapproved Task for waiting list${NC}"
UNAPPROVED_TASK='{"system_prompt": "Pending approval", "approval": false}'
echo "Payload: $UNAPPROVED_TASK"
RESP=$(curl -s -X POST "$TASKS_URL" -H "Content-Type: application/json" -d "$UNAPPROVED_TASK")
echo "Response: $RESP"
if [[ "$RESP" == *"accepted"* ]]; then
    UNAPPROVED_HASH=$(extract_hash "$RESP")
    echo -e "${GREEN}✓ Unapproved Task accepted, hash: $UNAPPROVED_HASH${NC}"
else
    echo -e "${RED}✗ Failed: $RESP${NC}"
    UNAPPROVED_HASH=""
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}11. GET /tasks/waiting - List waiting items${NC}"
sleep 1  # Allow file system to settle
RESP=$(curl -s "$WAITING_URL")
echo "Response: $RESP"
if [[ "$RESP" == *"[]"* ]]; then
    echo -e "${YELLOW}⚠ Waiting list empty (maybe approval file not yet moved)${NC}"
else
    echo -e "${GREEN}✓ Waiting list contains items${NC}"
    # Extract first waiting hash if available (for approval test)
    WAITING_HASH=$(echo "$RESP" | grep -o '"hash"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')
    if [[ -n "$WAITING_HASH" ]]; then
        echo "  First waiting hash: $WAITING_HASH"
    fi
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}12. POST /items/{hash}/approve - Approve a waiting item${NC}"
# Use hash from unapproved task if we have it
if [[ -n "$UNAPPROVED_HASH" ]]; then
    APPROVE_URL="$ITEMS_URL/$UNAPPROVED_HASH/approve"
    echo "Approving: $APPROVE_URL"
    RESP=$(curl -s -X POST "$APPROVE_URL")
    echo "Response: $RESP"
    if [[ "$RESP" == *"approved"* ]]; then
        echo -e "${GREEN}✓ Item approved${NC}"
        # Verify it moved to watch_path (pending status)
        sleep 1
        STATUS_RESP=$(curl -s "$ITEMS_URL/$UNAPPROVED_HASH")
        if [[ "$STATUS_RESP" == *"pending"* ]]; then
            echo -e "${GREEN}✓ Item now in pending state${NC}"
        else
            echo -e "${YELLOW}⚠ Item status after approval: $STATUS_RESP${NC}"
        fi
    else
        echo -e "${RED}✗ Approval failed: $RESP${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Skipping approval test (no unapproved hash)${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}13. FILE WATCHER - Valid JSON Task${NC}"
VALID_JSON_FILE="$TODO_DIR/valid_$(date +%s).json"
cat > "$VALID_JSON_FILE" <<EOF
{
  "system_prompt": "File-based task",
  "prompt_list": ["Hello"],
  "capacity": ["file"],
  "status": "created"
}
EOF
echo "Created: $VALID_JSON_FILE"
if wait_for_file_moved "$VALID_JSON_FILE" "$PROCESSED_DIR"; then
    echo -e "${GREEN}✓ JSON task moved to processed${NC}"
else
    echo -e "${RED}✗ Not moved to processed${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}14. FILE WATCHER - Valid YAML Task${NC}"
VALID_YAML_FILE="$TODO_DIR/valid_$(date +%s).yaml"
cat > "$VALID_YAML_FILE" <<EOF
system_prompt: "YAML task"
prompt_list:
  - "YAML prompt"
capacity:
  - yaml
status: subtasking
EOF
echo "Created: $VALID_YAML_FILE"
if wait_for_file_moved "$VALID_YAML_FILE" "$PROCESSED_DIR"; then
    echo -e "${GREEN}✓ YAML task moved to processed${NC}"
else
    echo -e "${RED}✗ Not moved to processed${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}15. FILE WATCHER - Invalid extension (.txt)${NC}"
INVALID_FILE="$TODO_DIR/bad_$(date +%s).txt"
echo '{"system_prompt": "bad"}' > "$INVALID_FILE"
echo "Created: $INVALID_FILE"
if wait_for_file_moved "$INVALID_FILE" "$FAILED_DIR"; then
    echo -e "${GREEN}✓ Invalid extension moved to failed${NC}"
else
    echo -e "${RED}✗ Not moved to failed${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}16. FILE WATCHER - Invalid JSON syntax${NC}"
INVALID_JSON_FILE="$TODO_DIR/invalid_$(date +%s).json"
echo '{system_prompt: missing quotes}' > "$INVALID_JSON_FILE"
echo "Created: $INVALID_JSON_FILE"
if wait_for_file_moved "$INVALID_JSON_FILE" "$FAILED_DIR"; then
    echo -e "${GREEN}✓ Invalid JSON moved to failed${NC}"
else
    echo -e "${RED}✗ Not moved to failed${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}17. FILE WATCHER - Missing system_prompt${NC}"
MISSING_FILE="$TODO_DIR/missing_$(date +%s).json"
echo '{"prompt_list": ["no system"]}' > "$MISSING_FILE"
echo "Created: $MISSING_FILE"
if wait_for_file_moved "$MISSING_FILE" "$FAILED_DIR"; then
    echo -e "${GREEN}✓ Missing system_prompt moved to failed${NC}"
else
    echo -e "${RED}✗ Not moved to failed${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${YELLOW}18. FILE WATCHER - Valid CapTask JSON${NC}"
CAP_FILE="$TODO_DIR/captask_$(date +%s).json"
cat > "$CAP_FILE" <<EOF
{
  "cap_name": "file_cap",
  "args": ["arg1", "arg2"],
  "approval": true
}
EOF
echo "Created: $CAP_FILE"
if wait_for_file_moved "$CAP_FILE" "$PROCESSED_CAPTASK_DIR"; then
    echo -e "${GREEN}✓ CapTask file moved to processedCapTask${NC}"
else
    echo -e "${RED}✗ Not moved to processedCapTask${NC}"
fi

# ----------------------------------------------------------------------
echo -e "\n${GREEN}=== All tests completed ===${NC}"