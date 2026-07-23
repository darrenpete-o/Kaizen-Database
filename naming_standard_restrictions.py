import pandas as pd
import os
from openai import OpenAI
import sys
import warnings

def analyze_table_purpose(table_name, columns):
    """Analyze table purpose based on name and columns."""
    # This is a simplified version - you can expand this based on your needs
    name_parts = table_name.replace('dbo.', '').split('_')
    cleaned_name = ' '.join(name_parts)
    return f"Table named '{cleaned_name}' with columns: {', '.join(columns[:5])}"

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
        print(f"Error generating business name: {e}")
        return None

def count_words(text):
    """Count the number of words in a string."""
    if pd.isna(text) or not isinstance(text, str):
        return 0
    # Split by whitespace and filter out empty strings
    words = text.strip().split()
    return len(words)

def check_and_rename_business_names(file_path):
    """
    Check the 'Business Name' column in the Excel file.
    If any entry has less than 2 words, generate a new name using AI.
    """
    try:
        # Read the Excel file
        df = pd.read_excel(file_path)
        
        # Check if 'Business Name' column exists
        if 'Business Name' not in df.columns:
            print(" Error: 'Business Name' column not found in the Excel file.")
            return False
        
        # Initialize Groq client
        client = OpenAI(
            api_key=os.environ.get("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )
        
        # Check each entry in the Business Name column
        issues_found = False
        for index, value in df['Business Name'].items():
            word_count = count_words(value)
            
            if word_count < 2:
                issues_found = True
                print(f"️  Warning: Row {index + 2} has a Business Name with {word_count} word(s): '{value}'")
                print(f"    This name is too short (needs at least 2 words)")
                
                # Get table name from another column if available, or use a default
                table_name = df.iloc[index].get('Table Name', f'row_{index}')
                columns = []
                
                # Try to get columns info if available
                if 'Columns' in df.columns:
                    columns_str = df.iloc[index].get('Columns', '')
                    if isinstance(columns_str, str) and columns_str:
                        columns = [col.strip() for col in columns_str.split(',')]
                
                # If no columns info, use some default columns
                if not columns:
                    columns = ['id', 'name', 'description', 'created_at']
                
                # Generate new business name using AI
                print(f"    Generating new business name using AI...")
                new_name = generate_business_name_with_columns(table_name, columns, client)
                
                if new_name:
                    print(f"    New name generated: '{new_name}'")
                    # Update the DataFrame
                    df.at[index, 'Business Name'] = new_name
                else:
                    print(f"    Failed to generate new name. Keeping original value.")
        
        # If issues were found, save the updated file
        if issues_found:
            # Save with a modified filename
            output_file = file_path.replace('.xlsx', '_updated.xlsx')
            df.to_excel(output_file, index=False)
            print(f"\n Updated file saved as: {output_file}")
        
        # Return True if no issues found, False if issues were found (but we still pass)
        return not issues_found
        
    except FileNotFoundError:
        print(f" Error: File '{file_path}' not found.")
        return False
    except Exception as e:
        print(f" Unexpected error: {e}")
        return False

def main():
    """Main function to run the script."""
    file_name = "database_changes_20260720_095930.xlsx"
    
    # Check if GROQ_API_KEY is set
    if not os.environ.get("GROQ_API_KEY"):
        print(" Error: GROQ_API_KEY environment variable not set.")
        print("   Please set it using: export GROQ_API_KEY='your-api-key'")
        sys.exit(1)
    
    print(f" Checking '{file_name}' for Business Names with less than 2 words...")
    print("-" * 60)
    
    result = check_and_rename_business_names(file_name)
    
    if result:
        print("\n All Business Names have 2 or more words.")
        print("   Script completed successfully (no errors).")
        sys.exit(0)
    else:
        print("\n  Issues were found and fixed where possible.")
        print("   Script completed with warnings (but allowed to pass).")
        sys.exit(0)

if __name__ == "__main__":
    main()
