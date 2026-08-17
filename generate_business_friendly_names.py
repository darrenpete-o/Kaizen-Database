import os
import re
import json
import time
from pathlib import Path
from openai import OpenAI
from google import genai
import pandas as pd

# Initialize Gemini client
client = genai.Client(
    api_key=os.environ.get("GOOGLE_API_KEY")
)
def get_tables_to_exclude(excel_file, sheet_name="Tables NOT in Source Code", column_name="TableName"):
    """
    Read table names from Excel file that should be excluded from visual diagram.
    Returns a set of table names to exclude.
    """
    tables_to_exclude = set()
    
    try:
        if not Path(excel_file).exists():
            print(f"Warning: Excel file '{excel_file}' not found!")
            print("   Continuing without excluding any tables.")
            return tables_to_exclude
        
        print(f"\nReading exclusion list from: {excel_file}")
        df = pd.read_excel(excel_file, sheet_name=sheet_name)
        
        print(f"Columns found: {list(df.columns)}")
        print(f"Total rows: {len(df)}")
        
        # Try to find the column with table names
        if column_name not in df.columns:
            # Try alternative column names
            possible_columns = ['TableName', 'Table', 'Name', 'table_name', 'TABLE_NAME', 'Table Name']
            found_col = None
            for col in possible_columns:
                if col in df.columns:
                    found_col = col
                    break
            
            if found_col:
                column_name = found_col
                print(f"Using column: {column_name}")
            else:
                # Use first column as fallback
                column_name = df.columns[0]
                print(f"Using first column as table name: {column_name}")
        
        # Extract table names
        for idx, row in df.iterrows():
            table_name = str(row[column_name]) if pd.notna(row[column_name]) else ''
            
            if table_name and table_name != 'nan':
                # Clean up the table name
                clean_table_name = table_name.strip()
                
                # Remove 'dbo.' prefix if present
                if clean_table_name.lower().startswith('dbo.'):
                    clean_table_name = clean_table_name[4:]
                
                # Remove any schema prefix if present
                if '.' in clean_table_name:
                    clean_table_name = clean_table_name.split('.')[-1]
                
                # Clean up any invalid characters
                clean_table_name = clean_table_name.replace('-', '_')
                clean_table_name = re.sub(r'[^a-zA-Z0-9_.]', '_', clean_table_name)
                
                tables_to_exclude.add(clean_table_name)
        
        print(f"Found {len(tables_to_exclude)} unique tables to exclude from visual diagram")
        
        # Show first 10 tables for verification
        if tables_to_exclude:
            print("\nTables to exclude from visual diagram (first 10):")
            for i, table in enumerate(sorted(list(tables_to_exclude))[:10], 1):
                print(f"   {i}. {table}")
            if len(tables_to_exclude) > 10:
                print(f"   ... and {len(tables_to_exclude) - 10} more")
        
        return tables_to_exclude
        
    except Exception as e:
        print(f"Error reading Excel file: {e}")
        import traceback
        traceback.print_exc()
        return tables_to_exclude

def extract_all_tables(dbml_content):
    """Extract all table names from DBML content."""
    pattern = r'Table\s+([a-zA-Z0-9_\.]+)\s*\{'
    tables = re.findall(pattern, dbml_content)
    return tables

def get_table_columns(dbml_content, table_name):
    """Extract column names from a table definition to provide context."""
    pattern = rf'Table\s+{re.escape(table_name)}\s*\{{([^}}]*)\}}'
    match = re.search(pattern, dbml_content, re.DOTALL)
    if match:
        table_body = match.group(1)
        columns = re.findall(r'^\s*([a-zA-Z0-9_]+)\s+[a-zA-Z0-9_]+', table_body, re.MULTILINE)
        columns.extend(re.findall(r'^\s*([a-zA-Z0-9_]+)\s*\[', table_body, re.MULTILINE))
        columns = list(set([c for c in columns if c and not c.startswith('note')]))
        return columns[:15]
    return []

def analyze_table_purpose(table_name, columns):
    """Try to infer table purpose from column names."""
    if not columns:
        return "Unknown"
    
    keywords = {
        'invoice': ['invoice', 'invoic', 'bill', 'payment', 'amount', 'tax', 'currency', 'due'],
        'employee': ['employee', 'user', 'person', 'name', 'email', 'phone', 'address', 'first', 'last'],
        'project': ['project', 'task', 'deliver', 'status', 'start', 'end', 'phase', 'code'],
        'expense': ['expense', 'cost', 'amount', 'currency', 'date', 'receipt', 'mileage', 'toll'],
        'customer': ['customer', 'client', 'company', 'account', 'contact', 'vendor'],
        'survey': ['survey', 'response', 'question', 'answer', 'rating', 'feedback'],
        'training': ['training', 'seminar', 'course', 'participant', 'instructor', 'certificate'],
        'co2': ['co2', 'emission', 'carbon', 'fuel', 'distance', 'transport', 'environment'],
        'consultant': ['consultant', 'hourly', 'rate', 'skill', 'experience', 'expertise'],
        'country': ['country', 'region', 'city', 'state', 'postal', 'zip', 'province'],
        'license': ['license', 'software', 'office', '365', 'microsoft'],
        'helpdesk': ['helpdesk', 'ticket', 'support', 'issue', 'incident', 'priority'],
        'website': ['website', 'page', 'content', 'form', 'newsletter', 'sector'],
    }
    
    scores = {}
    column_lower = [c.lower() for c in columns]
    
    for category, keywords_list in keywords.items():
        score = 0
        for keyword in keywords_list:
            for col in column_lower:
                if keyword in col:
                    score += 1
        if score > 0:
            scores[category] = score
    
    if scores:
        best_category = max(scores, key=scores.get)
        return best_category.title()
    
    return "Data"

def generate_business_name_with_columns(table_name, columns, client):
    """Get business-friendly name with column context."""
    
    clean_name = table_name.replace('dbo.', '').replace('_', ' ')
    purpose_hint = analyze_table_purpose(table_name, columns)
    
    column_list = ', '.join(columns[:10])
    if len(columns) > 10:
        column_list += f' and {len(columns) - 10} more columns'
    
    is_data_group = 'xdatagroup' in table_name.lower()
    table_type = "custom data group" if is_data_group else "table"
    
    prompt = f"""Given this database {table_type}: "{table_name}"

**CRITICAL**: This MUST have a UNIQUE, MEANINGFUL business name. Do NOT use "Data Group", "Information", "Details", or any generic term.

The columns in this table include:
{column_list}

Analysis suggests this relates to: {purpose_hint}

Based on the columns above, what specific business entity or concept does this table represent?

Rules:
- Provide a SPECIFIC, DESCRIPTIVE name (2-4 words)
- Use title case (e.g., "Project Expense Records")
- Look for patterns in the columns to determine actual purpose

**Return ONLY the business name, nothing else. No quotes, no explanations.**

Business name:"""
    
    try:
        response = client.chat.completions.create(
            model="gemini-3.6-flash",
            messages=[
                {"role": "system", "content": "You are a database naming expert. You analyze table structures and provide SPECIFIC, MEANINGFUL business names. Never use generic terms like 'Data Group' or 'Information' alone."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=30
        )
        business_name = response.choices[0].message.content.strip()
        business_name = business_name.strip('"\'')
        return business_name
    except Exception as e:
        print(f"Error: {e}")
        return None

def parse_views_comprehensively(views_sql_content):
    """
    Comprehensive parsing of views to find table business names.
    Returns a dict: {table_name: business_name}
    """
    table_names = {}
    
    # SQL keywords to skip
    sql_keywords = {'ON', 'INNER', 'LEFT', 'RIGHT', 'FULL', 'OUTER', 'JOIN', 'WHERE', 'AND', 'OR', 
                    'FROM', 'SELECT', 'AS', 'WITH', 'UNION', 'FOR', 'TO', 'IN', 'BY', 
                    'WHEN', 'THEN', 'ELSE', 'END', 'CASE', 'OVER', 'PARTITION', 'ORDER', 'GROUP',
                    'TOP', 'PERCENT', 'DISTINCT', 'ROW_NUMBER', 'OVER', 'PARTITION', 'VALUES'}
    
    print("\n Method 1: Finding explicit table aliases...")
    
    # Pattern 1: [dbo].[table_name] AS alias
    pattern1 = r'\[dbo\]\.\[([a-zA-Z0-9_]+)\]\s+AS\s+([a-zA-Z0-9_]+)'
    matches = re.findall(pattern1, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} -> {business_name}")
    
    print("\n Method 2: Finding table purposes from JOIN aliases...")
    
    # Pattern 2a: JOIN table_name AS alias
    pattern2a = r'JOIN\s+\[([a-zA-Z0-9_]+)\]\s+AS\s+([a-zA-Z0-9_]+)'
    matches = re.findall(pattern2a, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} -> {business_name}")
    
    # Pattern 2b: JOIN table_name alias (without AS)
    pattern2b = r'JOIN\s+\[([a-zA-Z0-9_]+)\]\s+([a-zA-Z0-9_]+)(?=\s+ON|\s+WHERE|\s+GROUP|\s+ORDER|\s+LEFT|\s+RIGHT|\s+INNER|\s+OUTER|\s+$)'
    matches = re.findall(pattern2b, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} -> {business_name}")
    
    # Pattern 2c: JOIN table_name alias (with AS, no brackets)
    pattern2c = r'JOIN\s+([a-zA-Z0-9_]+)\s+AS\s+([a-zA-Z0-9_]+)'
    matches = re.findall(pattern2c, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords and table not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} -> {business_name}")
    
    # Pattern 2d: JOIN table_name alias (without AS, no brackets)
    pattern2d = r'JOIN\s+([a-zA-Z0-9_]+)\s+([a-zA-Z0-9_]+)(?=\s+ON|\s+WHERE|\s+GROUP|\s+ORDER|\s+LEFT|\s+RIGHT|\s+INNER|\s+OUTER|\s+$)'
    matches = re.findall(pattern2d, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords and table not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} -> {business_name}")
    
    print("\n Method 3: Finding table purposes from FROM aliases...")
    
    # Pattern 3a: FROM table_name alias
    pattern3a = r'FROM\s+\[([a-zA-Z0-9_]+)\]\s+([a-zA-Z0-9_]+)(?=\s+LEFT|\s+RIGHT|\s+INNER|\s+OUTER|\s+WHERE|\s+GROUP|\s+ORDER|\s+$)'
    matches = re.findall(pattern3a, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} -> {business_name}")
    
    # Pattern 3b: FROM table_name alias (no brackets)
    pattern3b = r'FROM\s+([a-zA-Z0-9_]+)\s+([a-zA-Z0-9_]+)(?=\s+LEFT|\s+RIGHT|\s+INNER|\s+OUTER|\s+WHERE|\s+GROUP|\s+ORDER|\s+$)'
    matches = re.findall(pattern3b, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords and table not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} -> {business_name}")
    
    print("\n Method 4: Finding column aliases that reveal table purpose...")
    
    # Pattern 4: Column aliases that reveal table purpose
    pattern4 = r'\[dbo\]\.\[([a-zA-Z0-9_]+)\]\.([a-zA-Z0-9_]+)\s+AS\s+([a-zA-Z0-9_]+)'
    matches = re.findall(pattern4, views_sql_content, re.IGNORECASE)
    for table, column, alias in matches:
        if alias.upper() not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            # Business terms that indicate a table's purpose
            business_terms = ['Company', 'Employee', 'Project', 'Invoice', 'Expense', 'Industry', 
                            'Category', 'Planner', 'Contact', 'Group', 'Account', 'Header', 'Code',
                            'Country', 'Id', 'Lid', 'Number', 'Name', 'Date', 'Template', 'Seminar',
                            'Trip', 'Receipt', 'Payment', 'Registration', 'Connection', 'Mapping',
                            'Status', 'Manager', 'Supervisor', 'Responsible', 'Owner', 'Type']
            if any(term in business_name for term in business_terms) or len(business_name) > 2:
                if table in table_names:
                    existing = table_names[table]
                    if len(existing) <= 2 or existing in ['Ful', 'Ems', 'Ldc']:
                        table_names[table] = business_name
                        print(f"  {table} -> {business_name} (replaced {existing})")
                else:
                    table_names[table] = business_name
                    print(f"  {table} -> {business_name}")
    
    print("\n Method 5: Finding tables from view names...")
    
    # Pattern 5: Some views are named after the table they primarily use
    pattern5 = r'CREATE\s+VIEW\s+\[?([a-zA-Z0-9_]+)\]?\s+AS\s+SELECT.*?FROM\s+\[?([a-zA-Z0-9_]+)\]?'
    matches = re.findall(pattern5, views_sql_content, re.IGNORECASE | re.DOTALL)
    for view_name, table in matches:
        if '_view' in view_name.lower():
            business_name = view_name.replace('_view', '').replace('_', ' ').title()
            if len(business_name) > 2 and business_name not in ['Vbluser', 'Ki']:
                if table not in table_names:
                    table_names[table] = business_name
                    print(f"  {table} -> {business_name} (from view {view_name})")
    
    print("\n Method 6: Finding explicit comments with table names...")
    
    # Pattern 6: Comments like -- xdatagroup74a2b33e = companies
    pattern6 = r'--\s*([a-zA-Z0-9_]+)\s*=\s*([a-zA-Z0-9_\s]+)'
    matches = re.findall(pattern6, views_sql_content, re.IGNORECASE)
    for table, description in matches:
        if description.strip() and len(description.strip()) > 1:
            business_name = description.strip().title()
            table_names[table] = business_name
            print(f"  {table} -> {business_name}")
    
    print("\n Method 7: Cleaning up table names from the table itself...")
    
    # Tables that appear in views and have self-explanatory names
    tables_to_clean = [
        'helpdesk_activity', 'helpdesk_ticket', 'helpdesk_scopes', 'helpdesk_sub_category',
        'website_newsletter', 'website_pages', 'website_content', 'website_forms',
        'website_products', 'website_sectors', 'website_solutions', 'website_sub_sector',
        'expenses_reviewers', 'planner_reviewers', 'invoice_payments', 'payment_conditions',
        'countries_iso', 'business_areas', 'association_names', 'member_types',
        'office_licenses', 'new_kim_employees', 'ki_surveys', 'kim_settings',
        'soft_skills', 'routes_distances', 'validated_routes',
        'xdatagroup1cbaedf3', 'xdatagroup44427259', 'xdatagroup455ae863',
        'xdatagroupdf856a4c', 'xdatagroup5f99096f', 'xdatagroup5a7e1860',
        'xdatagroup17bd9dde', 'xdatagroup86fd9c45', 'xdatagroupde529862',
        'xdatagroup353ea8c1'
    ]
    
    business_name_map = {
        'xdatagroup1cbaedf3': 'PO Invoices',
        'xdatagroup44427259': 'Action Points',
        'xdatagroup455ae863': 'Connected Consultant',
        'xdatagroupdf856a4c': 'Exchange Rates',
        'xdatagroup5f99096f': 'Team Filters',
        'xdatagroup5a7e1860': 'Deductions',
        'xdatagroup17bd9dde': 'Industry Mappings',
        'xdatagroup86fd9c45': 'Core Technology Mappings',
        'xdatagroupde529862': 'Pilar Mappings',
        'xdatagroup353ea8c1': 'Employee Team Lids',
    }
    
    for table_name in tables_to_clean:
        if table_name in views_sql_content and table_name not in table_names:
            if table_name in business_name_map:
                business_name = business_name_map[table_name]
            else:
                business_name = table_name.replace('_', ' ').title()
            table_names[table_name] = business_name
            print(f"  {table_name} -> {business_name}")
    
    # Remove duplicates
    unique_names = {}
    seen_tables = set()
    
    for table, name in table_names.items():
        if table not in seen_tables:
            unique_names[table] = name
            seen_tables.add(table)
    
    return unique_names

def add_notes_to_dbml(dbml_content, table_names_with_notes):
    """Add notes to DBML content for each table at the top of the table body."""
    
    lines = dbml_content.split('\n')
    new_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        table_match = re.match(r'Table\s+([a-zA-Z0-9_\.]+)\s*\{', line)
        if table_match:
            table_name = table_match.group(1)
            
            # Check if this table already has a note
            has_note = False
            brace_count = 0
            j = i + 1
            while j < len(lines):
                current_line = lines[j].strip()
                brace_count += current_line.count('{') - current_line.count('}')
                if 'note:' in current_line.lower():
                    has_note = True
                    break
                if brace_count < 0:
                    break
                j += 1
            
            if not has_note and table_name in table_names_with_notes:
                business_name = table_names_with_notes[table_name]
                
                new_lines.append(line)
                new_lines.append(f'  note: "{business_name}"')
                
                i += 1
                while i < len(lines):
                    current_line = lines[i]
                    if current_line.strip() == '}':
                        break
                    if 'note:' not in current_line.lower():
                        new_lines.append(current_line)
                    i += 1
                
                if i < len(lines):
                    new_lines.append(lines[i])
                
                i += 1
                continue
            
            new_lines.append(line)
        else:
            new_lines.append(line)
        
        i += 1
    
    return '\n'.join(new_lines)

def create_diagram_view(dbml_content, tables_to_exclude):
    """
    Create a DiagramView that excludes the specified tables from the visual diagram.
    This keeps all tables in the DBML but hides the excluded ones from the diagram.
    """
    if not tables_to_exclude:
        return dbml_content
    
    # Extract all table names from the DBML
    all_tables = extract_all_tables(dbml_content)
    
    # Filter out excluded tables
    included_tables = []
    for table in all_tables:
        clean_table_name = table.replace('dbo.', '')
        if clean_table_name not in tables_to_exclude and table not in tables_to_exclude:
            included_tables.append(table)
    
    print(f"\nCreating DiagramView with {len(included_tables)} tables (excluding {len(tables_to_exclude)})")
    
    # Build the DiagramView
    diagram_view = "\n\n// ================================================\n"
    diagram_view += "// AUTO-GENERATED DIAGRAM VIEW\n"
    diagram_view += f"// Excludes {len(tables_to_exclude)} tables from the visual diagram\n"
    diagram_view += "// ================================================\n"
    diagram_view += 'DiagramView "Main View" {\n'
    diagram_view += "  Tables {\n"
    
    for table in sorted(included_tables):
        diagram_view += f"    {table}\n"
    
    diagram_view += "  }\n"
    diagram_view += "}\n"
    
    # Remove any existing DiagramView blocks
    dbml_content = re.sub(r'DiagramView\s+"[^"]*"\s*\{[^}]*\}', '', dbml_content, flags=re.DOTALL)
    dbml_content = re.sub(r'\n\s*\n\s*\n', '\n\n', dbml_content)
    
    # Append the new DiagramView
    return dbml_content + diagram_view

def create_default_view(updated_content):
    # create the default view (Kim Overview Map)
    default_view_overview_map = """
//DEFAULT VIEW 
table Companies_and_Employees [headercolor: #8B0000]{
  note: '''
    Companies & Employees: 

        Organization structure 
        and user management
  '''
  string attributes
}

table Projects [headercolor: #9DCCCC]{
  note: '''
    Projects: 
    
        Project lifecycle from
        creation to completion
  '''
  string attributes
}

table Planning_and_delivery [headercolor: #4A0000]{
  note: '''
    Planning & Delivery: 
    
        Resource planning and
        workshop delivery
  '''
  string attributes
}

table Expenses_and_Trips [headercolor: #9Ceecd]{
  note: '''
    Expenses & Trips: 
    
        Expense reporting and 
        approval workflows
  '''
  string attributes
}

table Invoices_and_Payments [headercolor: #9Cadce]{
  note: '''
    Invoices & Payments: 
    
        Billing and financial 
        management
  '''
  string attributes
}

table CO2_Reporting [headercolor: #9Cff00]{
  note: '''
    CO₂ Reporting: 
    
        Carbon emissions tracking 
        and reporting
  '''
  string attributes
}

table Helpdesk [headercolor: #901cdc]{
  note: '''
    Helpdesk: 
    
        Support ticket 
        management
  '''
  string attributes
}

table Analytics [headercolor: #4d1cdc]{
  note: '''
    Analytics: 
    
        Business intelligence 
        and reporting
  '''
  string attributes
}

Ref: Companies_and_Employees.string < Projects.string [color: #8B0000]
Ref: Projects.string < Planning_and_delivery.string [color: #9DCCCC]
Ref: Planning_and_delivery.string < Expenses_and_Trips.string [color: #4A0000]
Ref: Expenses_and_Trips.string < Invoices_and_Payments.string [color: #9Ceecd]
Ref: Invoices_and_Payments.string < CO2_Reporting.string [color: #9Cadce]
Ref: CO2_Reporting.string < Helpdesk.string [color: #9Cff00]
Ref: Helpdesk.string < Analytics.string [color: #901cdc]

DiagramView Default {
  tables {
    Companies_and_Employees
    Projects
    Planning_and_delivery
    Expenses_and_Trips
    Invoices_and_Payments
    CO2_Reporting
    Helpdesk
    Analytics
  }
}
"""
    # add the default view to the updated content
    return updated_content + "\n" + default_view_overview_map

def create_simplified_dbml(output_file):
    prompt = f"""
        For each table In the following file, ONLY return the table name and it's correspomding business name (the note of each table).
        ***IMPORTANT***: Do so in this manner: tablename = businessname

        DBML file for you to process:
        {output_file}
"""
    try:
        #Ask AI
        response = client.chat.completions.create(
            model = "llama-3.1-8b-instant",
            messages = [
                    {"role": "system", "content": "You are a specialist at reading the table name and the note attached to it which is its business name"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=30
            )
        dbml_simplified = response.choices[0].message.content.strip()
        return dbml_simplified
    except Exception as e:
        #Throw an error
        print(f"Error: {e}")
        return None
    
def create_domains(simplified_dbml, updated_content):
    prompt = f"""
        Given this list of Domains:
        1) Companies & Organization [color: #2C3E50]
        2) Employees & Access [color: #3498DB]
        3) Projects [color: #E67E22]
        4) Planning & Delivery [color: #1ABC9C]
        5) Expenses & Travel [color: #F1C40F]
        6) Invoices & Finance [color: #27AE60]
        7) CO₂ Management [color: #2ECC71]
        8) Helpdesk [color: #E74C3C]
        9) CRM & HubSpot [color: #FF7B7B]
        10) Website [color: #9B59B6]
        11) Reference & Configuration [color: #95A5A6]
        12) Audit & Integration [color: #34495E]
        13) Legacy or Unknown [color: #7F8C8D]
        
        Task: Split the tables from the DBML file into the appropriate domains based on the table name, notes, and content.
        
        ***IMPORTANT: Do NOT return the original DBML content. ONLY return the newly created TableGroup, DiagramView, and Ref blocks for each domain.***
        ***CRITICAL RULE***: You MUST use the EXACT table names as they appear on the LEFT side of the "=" sign in the simplified DBML I provided. DO NOT use the business names on the right side.
            DO NOT include the business names, DO NOT include the "=" sign, DO NOT include anything after the "="
            
        For example, if you see:
        invoice_table = Invoices
        payment_records = Payments

        You MUST use "invoice_table" and "payment_records" in the TableGroup, DiagramView, and Ref blocks - NOT "Invoices" and "Payments".
        
        RULES:
        1. Each table must be assigned to exactly ONE domain based on its primary purpose.
        2. Tables that don't clearly fit any domain should go to "Legacy or Unknown" (domain #13).
        3. For each domain, include ONLY references between tables that are BOTH in that same domain.
        4. Exclude any references that connect to tables in other domains.
        
        The format should be this for each domain:
            //*incrementing number starting from 00* *-* *Domain name*
            TableGroup *Domain name* [color: *Domain name color*]{{
              *List of all the tables that go in this domain*
            }}

            DiagramView "*incrementing number starting from 00* *-* *Domain name*"{{
              tables {{
                *List of all the tables that go in this domain*
              }}
              TableGroups {{*Domain name*}}
            }}

            Ref: *The exact same references taken from my dbml file* [color: *Domain name color*]
        ***FOR EXAMPLE***:
        //00 - Sales
        TableGroup sales [color: #4d1cdc]{{
          Helpdesk
          CO2_Reporting
        }}
        
        DiagramView "00 - sales"{{
          tables {{
            Helpdesk
            CO2_Reporting
          }}
          TableGroups {{sales}}
        }}

        Ref: CO2_Reporting.string < Helpdesk.string [color: #4d1cdc]

    ***VERY IMPORTANT***: DO NOT return ANYTHING except for EXACTLY what I have asked and do not wrap your response in any sort of quotations

    Now process this DBML content:
    {simplified_dbml} 
"""
    try:
        #Ask AI
        response = client.chat.completions.create(
            model = "llama-3.1-8b-instant",
            messages = [
                    {"role": "system", "content": "You are a senior database architect with 15+ years of experience in data modeling and system organization. Your expertise is in analyzing database schemas, understanding table relationships, and logically grouping tables into functional domains. You are meticulous, precise, and always follow formatting rules exactly."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=2000
            )
        domains = response.choices[0].message.content.strip()
        return updated_content + "\n\n" + domains
    except Exception as e:
        #Throw an error
        print(f"Error: {e}")
        return None
        
        

def process_dbml_file(input_file, output_file, views_sql_file, excel_file="tables_missing_from_source_code.xlsx"):
    """Main function to process the DBML file."""
    
    # Step 1: Get tables to exclude from Excel
    tables_to_exclude = get_tables_to_exclude(excel_file)
    
    # Step 2: Read the DBML content
    print(f"\nReading DBML file: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        dbml_content = f.read()
    
    # Step 3: Extract business names from views
    print(f"\nReading views file: {views_sql_file}")
    with open(views_sql_file, 'r', encoding='utf-8') as f:
        views_content = f.read()
    
    business_names_from_views = parse_views_comprehensively(views_content)
    
    print(f"\nFound {len(business_names_from_views)} business names from views")
    
    # Step 4: Get tables from the DBML
    tables = extract_all_tables(dbml_content)
    print(f"\nFound {len(tables)} tables in DBML")
    
    # Step 5: Check which tables already have notes
    tables_with_notes = []
    lines = dbml_content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        table_match = re.match(r'Table\s+([a-zA-Z0-9_\.]+)\s*\{', line)
        if table_match:
            table_name = table_match.group(1)
            has_note = False
            brace_count = 0
            j = i + 1
            while j < len(lines):
                current_line = lines[j].strip()
                brace_count += current_line.count('{') - current_line.count('}')
                if 'note:' in current_line.lower():
                    has_note = True
                    break
                if brace_count < 0:
                    break
                j += 1
            if has_note:
                tables_with_notes.append(table_name)
        i += 1
    
    tables_to_process = [t for t in tables if t not in tables_with_notes]
    print(f"Tables with notes already: {len(tables_with_notes)}")
    print(f"Need to process: {len(tables_to_process)} tables")
    
    if not tables_to_process:
        print("All tables already have notes!")
        # Still create the DiagramView
        dbml_content = create_diagram_view(dbml_content, tables_to_exclude)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(dbml_content)
        print(f"\nUpdated file saved to: {output_file}")
        print(f"Excluded {len(tables_to_exclude)} tables from visual diagram")
        return
    
    # Step 6: Process remaining tables
    table_names_with_notes = {}
    view_name_count = 0
    ai_name_count = 0
    
    for idx, table in enumerate(tables_to_process, 1):
        table_without_prefix = table.replace('dbo.', '')
        
        if table in business_names_from_views:
            table_names_with_notes[table] = business_names_from_views[table]
            view_name_count += 1
            print(f" Using view name: {table} -> {business_names_from_views[table]}")
        elif table_without_prefix in business_names_from_views:
            table_names_with_notes[table] = business_names_from_views[table_without_prefix]
            view_name_count += 1
            print(f" Using view name: {table} -> {business_names_from_views[table_without_prefix]}")
        else:
            print(f"\n No view name found for {table}, using AI with columns...")
            columns = get_table_columns(dbml_content, table)
            business_name = generate_business_name_with_columns(table, columns, client)
            if business_name:
                table_names_with_notes[table] = business_name
                ai_name_count += 1
                print(f"  -> {business_name}")
            else:
                fallback = table.replace('dbo.', '').replace('_', ' ').title()
                table_names_with_notes[table] = fallback
                ai_name_count += 1
                print(f"  -> Using fallback: {fallback}")
            
            if idx < len(tables_to_process):
                time.sleep(0.5)
    
    # Step 7: Add notes to DBML
    updated_content = add_notes_to_dbml(dbml_content, table_names_with_notes)
    under_token_limit_content = dbml_content
    
    # Step 8: Create DiagramView that excludes tables
    updated_content = create_diagram_view(updated_content, tables_to_exclude)
    
    # Step 9: create the default view (Kim Overview Map)
    updated_content = create_default_view(updated_content)

    # save the current dbml
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(updated_content)
        print("Saved Current Content")

    # Ask AI to simplify the dbml file so that it's {table_name} = {business_name}
    simplified_dbml = create_simplified_dbml(output_file)

    # read the file back
    #with open(output_file, 'r', encoding='utf-8') as f:
        #input_current_dbml_file = f.read()
        
    # Step 10: Filter tables into domains using AI
    updated_content = create_domains(simplified_dbml, updated_content)
    
    # Step 11: Save the updated file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(updated_content)
    
    print(f"\nComplete! Updated file saved to: {output_file}")
    print(f"Added notes to {len(table_names_with_notes)} tables")
    print(f"  - From views: {view_name_count}")
    print(f"  - From AI/fallback: {ai_name_count}")
    print(f"Excluded {len(tables_to_exclude)} tables from visual diagram (tables kept in DBML)")

def main():
    input_file = 'schema_diagram.dbml'  
    output_file = 'schema_diagram_with_aliases_as_notes.dbml'
    views_sql_file = 'views_Script.sql'
    excel_file = 'tables_missing_from_source_code.xlsx'
    
    if not Path(input_file).exists():
        print(f"Error: Input file '{input_file}' not found!")
        return
    
    if not Path(views_sql_file).exists():
        print(f"Error: Views file '{views_sql_file}' not found!")
        return
    
    process_dbml_file(input_file, output_file, views_sql_file, excel_file)

if __name__ == "__main__":
    main()
