import os
from dotenv import load_dotenv
from anthropic import Anthropic

# 1. Load the variables from the .env file in your root directory
load_dotenv()

# 2. Now os.environ will successfully find the key you set!
api_key = os.environ.get("ANTHROPIC_API_KEY")

# 3. Initialize the client and list the models
client = Anthropic(api_key=api_key)
models_page = client.models.list()

for model in models_page.data:
    print(f"Model ID: {model.id} - Display Name: {model.display_name}")