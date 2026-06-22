import sys

path = r"c:\Users\bao.nguyen\Documents\ChatBotProject\app_chatbot.py"
with open(path, 'r', encoding='utf-8') as f:
    lines = f.read().splitlines()

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    if '"user_roles": user_roles or [],' in line:
        new_lines.append(line)
        new_lines.append('        "allowed_departments": allowed_departments or [],')
        i += 1
    elif 'user_department=current_user["department"],' in line:
        new_lines.append(line)
        new_lines.append('                        user_roles=current_user["roles"],')
        new_lines.append('                        allowed_departments=current_user.get("allowed_departments", [])')
        i += 2
    else:
        new_lines.append(line)
        i += 1

with open(path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(new_lines))
