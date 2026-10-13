
# Fix logger.warning f-string indentation in publisher_agent.py (lines 416-417, idx 415-416)
with open('app/agents/publisher_agent.py', 'rb') as f:
    content = f.read()
lines = content.split(b'\r\n')

# Find the lines with Vietnamese warning text that have bad indentation
for i, line in enumerate(lines):
    if b'UploadRetry' in line or (b'ph\xc3\xaan m' in line and b'th\xe1\xbb\xad l\xe1\xba\xa1i' in line):
        print(f'{i+1}: {line[:100]}')

# Fix: lines 416 and 417 (idx 415-416) should be indented to 32 spaces
idx_416 = None
for i, line in enumerate(lines):
    if b'UploadRetry' in line:
        idx_416 = i
        break

if idx_416:
    # Fix both f-string lines
    lines[idx_416] = b'                                ' + lines[idx_416].lstrip()
    lines[idx_416 + 1] = b'                                ' + lines[idx_416 + 1].lstrip()
    new_content = b'\r\n'.join(lines)
    with open('app/agents/publisher_agent.py', 'wb') as f:
        f.write(new_content)
    print("FIXED f-string indentation")
