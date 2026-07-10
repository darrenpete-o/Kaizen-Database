import os
import pyodbc
from pathlib import Path

def export_views_to_file(connection_string, output_file='views_Script.sql'):
    """
    Connect to SQL Server database and export all view definitions.
    
    Args:
        connection_string: ODBC connection string
        output_file: Path to output SQL file
    """
    
    try:
        # Connect to database
        print(f"🔗 Connecting to database...")
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        
        # Get all view names
        print("📊 Fetching view names...")
        cursor.execute("""
            SELECT 
                TABLE_SCHEMA,
                TABLE_NAME
            FROM 
                INFORMATION_SCHEMA.VIEWS
            WHERE 
                TABLE_SCHEMA NOT IN ('sys', 'information_schema')
            ORDER BY 
                TABLE_SCHEMA, TABLE_NAME
        """)
        
        views = cursor.fetchall()
        
        if not views:
            print("⚠️  No views found in the database!")
            return
        
        print(f"✅ Found {len(views)} views")
        
        # Build the SQL script
        print("📝 Building view definitions...")
        sql_script = []
        sql_script.append("-- =============================================")
        sql_script.append("-- VIEW DEFINITIONS EXPORT")
        sql_script.append("-- Generated: " + __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        sql_script.append("-- Total Views: " + str(len(views)))  # ✅ Fixed - added closing parenthesis
        sql_script.append("-- =============================================")
        sql_script.append("")
        
        for schema, view_name in views:
            print(f"  Processing: {schema}.{view_name}")
            
            # Get view definition
            cursor.execute("""
                SELECT 
                    OBJECT_DEFINITION(OBJECT_ID(?))
            """, (f"{schema}.{view_name}",))
            
            definition = cursor.fetchone()[0]
            
            if definition:
                sql_script.append(f"-- =============================================")
                sql_script.append(f"-- View: {schema}.{view_name}")
                sql_script.append(f"-- =============================================")
                sql_script.append("")
                
                # Add CREATE OR ALTER VIEW
                definition = definition.replace(
                    f"CREATE VIEW {schema}.{view_name}",
                    f"CREATE OR ALTER VIEW {schema}.{view_name}"
                ).replace(
                    f"CREATE VIEW [{schema}].[{view_name}]",
                    f"CREATE OR ALTER VIEW [{schema}].[{view_name}]"
                )
                
                sql_script.append(definition)
                sql_script.append("")
                sql_script.append("GO")
                sql_script.append("")
        
        # Write to file
        print(f"💾 Writing to {output_file}...")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(sql_script))
        
        print(f"✅ Successfully exported {len(views)} views to {output_file}")
        
        # Show file size
        file_size = Path(output_file).stat().st_size / 1024  # KB
        print(f"📁 File size: {file_size:.2f} KB")
        
        cursor.close()
        conn.close()
        
    except pyodbc.Error as e:
        print(f"❌ Database error: {e}")
        return
    except Exception as e:
        print(f"❌ Error: {e}")
        return

def main():
    """
    Main function - configure your connection settings here.
    """
    
    # ============================================================
    # CONFIGURATION - Edit these values for your environment
    # ============================================================
    
    # Using Windows Authentication (Trusted Connection)
    connection_string = (
        "DRIVER={SQL SERVER};"
        "SERVER=JARVIS;"
        "DATABASE=KIMdb;"
        "Trusted_Connection=yes;"
    )

    # ============================================================
    # END OF CONFIGURATION
    # ============================================================
    
    # Output file path
    output_file = 'views_Script.sql'
    
    # Run the export
    export_views_to_file(connection_string, output_file)

if __name__ == "__main__":
    main()
