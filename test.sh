#!/bin/bash
# Test script: send REST request, create a file, and wait for internal generation

set -e

echo "=== Testing Todo Receiver ==="

# Check if service is running
if ! curl -s http://localhost:8080/docs > /dev/null; then
    echo "Error: Service not running. Start main.py first."
    exit 1
fi

# 1. Test REST API
echo "Sending REST todo..."
curl -X POST http://localhost:8080/todo \
    -H "Content-Type: application/json" \
    -d '{"title": "Test REST", "description": "From test script"}'
echo -e "\n"

# 2. Test file watcher
TODO_DIR=${TODO_WATCH_DIR:-./todo_folder}
TEST_FILE="$TODO_DIR/test_todo_$(date +%s).txt"
echo "Creating test file: $TEST_FILE"
echo "This is a test todo from file." > "$TEST_FILE"
echo "File created. Check logs for processing."
sleep 2

# 3. Internal generation (will happen every 30s, we can't trigger directly)
echo "Internal generation will occur every 30 seconds (check logs)."

echo "=== Test Complete ==="