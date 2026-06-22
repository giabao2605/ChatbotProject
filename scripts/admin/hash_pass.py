import os

files_to_update = [
    r'c:\Users\bao.nguyen\Documents\ChatBotProject\Mech_Chatbot_DB.sql',
    r'c:\Users\bao.nguyen\Documents\ChatBotProject\scripts\migrate_phase5.py'
]

for file in files_to_update:
    if not os.path.exists(file): continue
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    content = content.replace("'admin123'", "'$2b$12$GjF79FWNuuNfl4VWOA28iOk4ubZWWd5OltSsAiZ5TgaWPz5UtAZpu'")
    content = content.replace("'123456'", "'$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2'")
    
    with open(file, 'w', encoding='utf-8') as f:
        f.write(content)
print('Done replacing.')
