import json
import sys

transcript_path = r"C:\Users\PoselyanovLaptop\.gemini\antigravity\brain\77b8e2a8-93e7-4276-842e-37e505a664ee\.system_generated\logs\transcript.jsonl"
output_path = "extracted_chunks.md"

with open(transcript_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

with open(output_path, 'w', encoding='utf-8') as out:
    for line in lines:
        try:
            data = json.loads(line)
            if 'tool_calls' in data:
                for call in data['tool_calls']:
                    if call['name'] in ['multi_replace_file_content', 'replace_file_content']:
                        args = call.get('args', {})
                        target = args.get('TargetFile', '')
                        if 'index.html' in target:
                            out.write(f"## Tool Call: {call['name']} on {target}\n\n")
                            if call['name'] == 'multi_replace_file_content':
                                for chunk in args.get('ReplacementChunks', []):
                                    out.write(f"### Chunk Start: {chunk.get('StartLine')} End: {chunk.get('EndLine')}\n")
                                    out.write("#### Target Content:\n```html\n" + chunk.get('TargetContent', '') + "\n```\n")
                                    out.write("#### Replacement Content:\n```html\n" + chunk.get('ReplacementContent', '') + "\n```\n\n")
                            else:
                                out.write(f"### Chunk Start: {args.get('StartLine')} End: {args.get('EndLine')}\n")
                                out.write("#### Target Content:\n```html\n" + args.get('TargetContent', '') + "\n```\n")
                                out.write("#### Replacement Content:\n```html\n" + args.get('ReplacementContent', '') + "\n```\n\n")
        except:
            pass

print("Done")
