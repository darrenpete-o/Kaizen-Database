import os
import re
import json
import time
from pathlib import Path
from openai import OpenAI

# Initialize Groq client
client = OpenAI(
    api_key=os.environ.get("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

def extract_table_names(dbml_content):
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
            model="llama-3.1-8b-instant",
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
                print(f"  {table} → {business_name}")
    
    print("\n Method 2: Finding table purposes from JOIN aliases...")
    
    # Pattern 2a: JOIN table_name AS alias
    pattern2a = r'JOIN\s+\[([a-zA-Z0-9_]+)\]\s+AS\s+([a-zA-Z0-9_]+)'
    matches = re.findall(pattern2a, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} → {business_name}")
    
    # Pattern 2b: JOIN table_name alias (without AS)
    pattern2b = r'JOIN\s+\[([a-zA-Z0-9_]+)\]\s+([a-zA-Z0-9_]+)(?=\s+ON|\s+WHERE|\s+GROUP|\s+ORDER|\s+LEFT|\s+RIGHT|\s+INNER|\s+OUTER|\s+$)'
    matches = re.findall(pattern2b, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} → {business_name}")
    
    # Pattern 2c: JOIN table_name alias (with AS, no brackets)
    pattern2c = r'JOIN\s+([a-zA-Z0-9_]+)\s+AS\s+([a-zA-Z0-9_]+)'
    matches = re.findall(pattern2c, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords and table not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} → {business_name}")
    
    # Pattern 2d: JOIN table_name alias (without AS, no brackets)
    pattern2d = r'JOIN\s+([a-zA-Z0-9_]+)\s+([a-zA-Z0-9_]+)(?=\s+ON|\s+WHERE|\s+GROUP|\s+ORDER|\s+LEFT|\s+RIGHT|\s+INNER|\s+OUTER|\s+$)'
    matches = re.findall(pattern2d, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords and table not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} → {business_name}")
    
    print("\n Method 3: Finding table purposes from FROM aliases...")
    
    # Pattern 3a: FROM table_name alias
    pattern3a = r'FROM\s+\[([a-zA-Z0-9_]+)\]\s+([a-zA-Z0-9_]+)(?=\s+LEFT|\s+RIGHT|\s+INNER|\s+OUTER|\s+WHERE|\s+GROUP|\s+ORDER|\s+$)'
    matches = re.findall(pattern3a, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} → {business_name}")
    
    # Pattern 3b: FROM table_name alias (no brackets)
    pattern3b = r'FROM\s+([a-zA-Z0-9_]+)\s+([a-zA-Z0-9_]+)(?=\s+LEFT|\s+RIGHT|\s+INNER|\s+OUTER|\s+WHERE|\s+GROUP|\s+ORDER|\s+$)'
    matches = re.findall(pattern3b, views_sql_content, re.IGNORECASE)
    for table, alias in matches:
        if alias.upper() not in sql_keywords and table not in sql_keywords:
            business_name = alias.replace('_', ' ').title()
            if len(business_name) > 1:
                table_names[table] = business_name
                print(f"  {table} → {business_name}")
    
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
                        print(f"  {table} → {business_name} (replaced {existing})")
                else:
                    table_names[table] = business_name
                    print(f"  {table} → {business_name}")
    
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
                    print(f"  {table} → {business_name} (from view {view_name})")
    
    print("\n Method 6: Finding explicit comments with table names...")
    
    # Pattern 6: Comments like -- xdatagroup74a2b33e = companies
    pattern6 = r'--\s*([a-zA-Z0-9_]+)\s*=\s*([a-zA-Z0-9_\s]+)'
    matches = re.findall(pattern6, views_sql_content, re.IGNORECASE)
    for table, description in matches:
        if description.strip() and len(description.strip()) > 1:
            business_name = description.strip().title()
            table_names[table] = business_name
            print(f"  {table} → {business_name}")
    
    print("\n Method 7: Cleaning up table names from the table itself...")
    
    # Tables that appear in views and have self-explanatory names
    tables_to_clean = [
        # Self-explanatory tables
        'helpdesk_activity', 'helpdesk_ticket', 'helpdesk_scopes', 'helpdesk_sub_category',
        'website_newsletter', 'website_pages', 'website_content', 'website_forms',
        'website_products', 'website_sectors', 'website_solutions', 'website_sub_sector',
        'expenses_reviewers', 'planner_reviewers', 'invoice_payments', 'payment_conditions',
        'countries_iso', 'business_areas', 'association_names', 'member_types',
        'office_licenses', 'new_kim_employees', 'ki_surveys', 'kim_settings',
        'soft_skills', 'routes_distances', 'validated_routes',
        
        # Tables that need proper business names
        'xdatagroup1cbaedf3',      # PO Invoices
        'xdatagroup44427259',      # Action Points
        'xdatagroup455ae863',      # Connected Consultant
        'xdatagroupdf856a4c',      # Exchange Rates
        'xdatagroup5f99096f',      # Team Filters
        'xdatagroup5a7e1860',      # Deductions
        'xdatagroup17bd9dde',      # Industry Mappings
        'xdatagroup86fd9c45',      # Core Technology Mappings
        'xdatagroupde529862',      # Pilar Mappings
        'xdatagroup353ea8c1',      # Employee Team Lids
    ]
    
    # Manual mapping for specific tables
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
        # Only add if the table appears in views and doesn't have a name yet
        if table_name in views_sql_content and table_name not in table_names:
            if table_name in business_name_map:
                business_name = business_name_map[table_name]
            else:
                business_name = table_name.replace('_', ' ').title()
            table_names[table_name] = business_name
            print(f"  {table_name} → {business_name}")
    
    # Remove duplicates - keep first occurrence
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

def process_dbml_file(input_file, output_file, views_sql_file):
    """Main function to process the DBML file."""
    
    # First, extract business names from views
    print(f"\n Reading views file: {views_sql_file}")
    with open(views_sql_file, 'r', encoding='utf-8') as f:
        views_content = f.read()
    
    business_names_from_views = parse_views_comprehensively(views_content)
    
    print(f"\n Found {len(business_names_from_views)} business names from views")
    
    # Print all found names for debugging
    if business_names_from_views:
        print("\n All names found in views:")
        for table, name in sorted(business_names_from_views.items()):
            print(f"  {table} → {name}")
    
    # Read DBML content
    with open(input_file, 'r', encoding='utf-8') as f:
        dbml_content = f.read()
    
    tables = extract_table_names(dbml_content)
    print(f"\n Found {len(tables)} tables in DBML")
    
    # Check which tables already have notes
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
        return
    
    # Try to get names from views first
    table_names_with_notes = {}
    view_name_count = 0
    ai_name_count = 0
    
    for idx, table in enumerate(tables_to_process, 1):
        table_without_prefix = table.replace('dbo.', '')
        
        if table in business_names_from_views:
            table_names_with_notes[table] = business_names_from_views[table]
            view_name_count += 1
            print(f" Using view name: {table} → {business_names_from_views[table]}")
        elif table_without_prefix in business_names_from_views:
            table_names_with_notes[table] = business_names_from_views[table_without_prefix]
            view_name_count += 1
            print(f" Using view name: {table} → {business_names_from_views[table_without_prefix]}")
        else:
            print(f"\n No view name found for {table}, using AI with columns...")
            columns = get_table_columns(dbml_content, table)
            business_name = generate_business_name_with_columns(table, columns, client)
            if business_name:
                table_names_with_notes[table] = business_name
                ai_name_count += 1
                print(f"  → {business_name}")
            else:
                fallback = table.replace('dbo.', '').replace('_', ' ').title()
                table_names_with_notes[table] = fallback
                ai_name_count += 1
                print(f"  → Using fallback: {fallback}")
            
            if idx < len(tables_to_process):
                time.sleep(0.5)
    
    updated_content = add_notes_to_dbml(dbml_content, table_names_with_notes)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(updated_content)
    
    print(f"\n Complete! Updated file saved to: {output_file}")
    print(f"Added notes to {len(table_names_with_notes)} tables")
    print(f"  - From views: {view_name_count}")
    print(f"  - From AI/fallback: {ai_name_count}")

def main():
    input_file = 'schema_diagram.dbml'  
    output_file = 'schema_diagram_with_aliases_as_notes.dbml'
    views_sql_file = 'views_Script.sql'
    
    if not Path(input_file).exists():
        print(f"Error: Input file '{input_file}' not found!")
        return
    
    if not Path(views_sql_file).exists():
        print(f"Error: Views file '{views_sql_file}' not found!")
        return
    
    process_dbml_file(input_file, output_file, views_sql_file)

if __name__ == "__main__":
    main()
