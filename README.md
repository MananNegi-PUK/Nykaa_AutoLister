# Nykaa Auto Lister Pro

An automated listing generator web application designed for Nykaa Fashion sellers to instantly map, validate, and build macro-enabled catalog template sheets from item color codes.

## Features

- **Automated Listing Generation**: Input item style color codes to dynamically generate ready-to-upload Nykaa templates.
- **In-Memory ZIP/XML Modifications**: Modifies worksheets directly at the XML level to preserve all drop-down validation rules, macros, and styles.
- **Dynamic Sizing Charts**: Automatically converts CM measurements from reference charts to inches (rounded to 1 decimal place) and maps them to appropriate columns.
- **Dynamic Category & Product Type Resolution**: Parses style codes against the Item Directory to dynamically resolve the category, Singularized Pack Contains, and Kids Product Type.
- **Continuous AI Learning Base**: Automatically learns column mappings and default values when a populated historical listing is uploaded.
- **System Diagnostics Validation**: Evaluates sheet structures, templates, and active database integrity before starting generation jobs.

## Local Setup

1. Install Python 3.10+
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the development server:
   ```bash
   python app.py
   ```
4. Access the web interface at `http://127.0.0.1:8000`

## Deployment to Railway & Supabase

1. Create a free PostgreSQL database on [Supabase](https://supabase.com/).
2. Copy the database connection URI (ensure you replace `[YOUR-PASSWORD]` with the database password).
3. Push this project repository to GitHub.
4. Deploy the repository on [Railway](https://railway.app/).
5. Under service settings, add the environment variable `DATABASE_URL` and paste the Supabase connection string.
6. Open your live app URL, go to the **Upload Center**, and upload:
   - Your **Master Item Directory**
   - Your **Content Sheet**
   - The active **Category Template** (`.xlsm`)
