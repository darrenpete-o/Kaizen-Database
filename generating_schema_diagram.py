import pyodbc
import re
from collections import defaultdict

def get_db_connection():
    """
    Establish connection to SQL Server using the provided connection details.
    """
    DRIVER_NAME = 'SQL Server'
    SERVER_NAME = 'JARVIS'
    DATABASE_NAME = 'kimDB'
    
    connection_string = f"""
        DRIVER={{{DRIVER_NAME}}};
        SERVER={{{SERVER_NAME}}};
        DATABASE={{{DATABASE_NAME}}};
        Trusted_Connection=yes;
    """
    
    return pyodbc.connect(connection_string)

def get_all_views(conn):
    """
    Get all view names from the database.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name 
        FROM sys.objects 
        WHERE type = 'V' 
        ORDER BY name
    """)
    return [row[0] for row in cursor.fetchall()]

def get_tables_from_views(conn, views_list):
    """
    Get all tables referenced in the given views from the database.
    This queries the system views to find dependencies.
    """
    tables = set()
    cursor = conn.cursor()
    
    if not views_list:
        return tables
    
    # Format the list of view names for the IN clause
    view_names = ','.join("'{}'".format(v) for v in views_list)
    
    # Query to find all tables referenced in views
    # Uses sys.sql_expression_dependencies which is the modern way
    query = """
    WITH ViewDependencies AS (
        SELECT 
            OBJECT_NAME(referencing_id) AS view_name,
            OBJECT_NAME(referenced_id) AS table_name
        FROM sys.sql_expression_dependencies
        WHERE OBJECT_NAME(referencing_id) IN ({})
        AND referenced_id IS NOT NULL
        AND OBJECTPROPERTY(referenced_id, 'IsUserTable') = 1
    )
    SELECT DISTINCT table_name
    FROM ViewDependencies
    ORDER BY table_name
    """
    
    try:
        cursor.execute(query.format(view_names))
        rows = cursor.fetchall()
        for row in rows:
            tables.add(row[0])
    except Exception as e:
        print(f"Error with sys.sql_expression_dependencies: {e}")
        print("Trying alternative method...")
        
        # Alternative method using sys.sql_dependencies (older but still works)
        query2 = """
        SELECT DISTINCT 
            OBJECT_NAME(d.referenced_major_id) AS table_name
        FROM sys.sql_dependencies d
        JOIN sys.objects v ON d.object_id = v.object_id
        WHERE v.type = 'V'
        AND v.name IN ({})
        AND d.referenced_major_id IS NOT NULL
        AND OBJECTPROPERTY(d.referenced_major_id, 'IsUserTable') = 1
        ORDER BY table_name
        """
        
        try:
            cursor.execute(query2.format(view_names))
            rows = cursor.fetchall()
            for row in rows:
                tables.add(row[0])
        except Exception as e2:
            print(f"Error with sys.sql_dependencies: {e2}")
            print("Falling back to parsing view definitions...")
            
            # Last resort - parse view definitions from sys.sql_modules
            for view_name in views_list:
                try:
                    cursor.execute("""
                        SELECT OBJECT_DEFINITION(OBJECT_ID(?))
                    """, (view_name,))
                    row = cursor.fetchone()
                    if row and row[0]:
                        view_def = row[0]
                        # Find table names in FROM and JOIN clauses
                        pattern = r'(?:FROM|JOIN)\s+(?:\[dbo\]\.)?\[?([a-zA-Z_][a-zA-Z0-9_]*)\]?'
                        matches = re.findall(pattern, view_def, re.IGNORECASE)
                        for table in matches:
                            if table.lower() not in ['select', 'where', 'on', 'and', 'or', 'as']:
                                tables.add(table)
                except Exception as e3:
                    print(f"Error parsing view {view_name}: {e3}")
    
    return tables

def get_table_columns(conn, table_name):
    """
    Get all columns for a table including primary key information.
    """
    cursor = conn.cursor()
    
    query = """
    SELECT 
        c.name AS column_name,
        t.name AS data_type,
        c.max_length,
        c.precision,
        c.scale,
        c.is_nullable,
        c.is_identity,
        c.is_computed,
        CASE WHEN pk.column_id IS NOT NULL THEN 1 ELSE 0 END AS is_primary_key
    FROM sys.columns c
    JOIN sys.types t ON c.user_type_id = t.user_type_id
    LEFT JOIN (
        SELECT ic.column_id, ic.object_id
        FROM sys.index_columns ic
        JOIN sys.indexes i ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        WHERE i.is_primary_key = 1
    ) pk ON c.object_id = pk.object_id AND c.column_id = pk.column_id
    WHERE c.object_id = OBJECT_ID(?)
    ORDER BY c.column_id
    """
    
    cursor.execute(query, (table_name,))
    columns = []
    for row in cursor.fetchall():
        # Clean up data type for display
        data_type = row[1]
        if data_type in ['nvarchar', 'varchar', 'char', 'nchar']:
            if row[2] == -1:
                data_type = f"{data_type}(max)"
            else:
                data_type = f"{data_type}({row[2]})"
        elif data_type in ['decimal', 'numeric']:
            data_type = f"{data_type}({row[3]},{row[4]})"
        
        columns.append({
            'name': row[0],
            'type': data_type,
            'is_nullable': row[5],
            'is_identity': row[6],
            'is_computed': row[7],
            'is_primary_key': row[8] == 1
        })
    
    return columns

def get_table_foreign_keys(conn, table_name):
    """
    Get foreign key relationships for a table.
    """
    cursor = conn.cursor()
    
    query = """
    SELECT 
        OBJECT_NAME(fk.parent_object_id) AS table_name,
        COL_NAME(fk.parent_object_id, fkc.parent_column_id) AS column_name,
        OBJECT_NAME(fk.referenced_object_id) AS referenced_table,
        COL_NAME(fk.referenced_object_id, fkc.referenced_column_id) AS referenced_column
    FROM sys.foreign_keys fk
    JOIN sys.foreign_key_columns fkc ON fk.object_id = fkc.constraint_object_id
    WHERE fk.parent_object_id = OBJECT_ID(?)
    """
    
    cursor.execute(query, (table_name,))
    relationships = []
    for row in cursor.fetchall():
        relationships.append({
            'from_table': row[0],
            'from_column': row[1],
            'to_table': row[2],
            'to_column': row[3]
        })
    
    return relationships

def get_relationships_from_views(conn, views_list):
    """
    Get relationships from view definitions by parsing JOIN conditions.
    """
    relationships = []
    cursor = conn.cursor()
    
    for view_name in views_list:
        try:
            cursor.execute("""
                SELECT OBJECT_DEFINITION(OBJECT_ID(?))
            """, (view_name,))
            row = cursor.fetchone()
            if row and row[0]:
                view_def = row[0]
                
                # Parse JOIN conditions
                # This is a fallback for relationships not captured by foreign keys
                pattern = r'ON\s+([^\s]+?)\s*=\s*([^\s]+?)(?:\s|$|\)|;)'
                matches = re.findall(pattern, view_def, re.IGNORECASE | re.DOTALL)
                
                for left, right in matches:
                    left_match = re.search(r'([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)', left)
                    right_match = re.search(r'([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)', right)
                    
                    if left_match and right_match:
                        from_table = left_match.group(1)
                        from_col = left_match.group(2)
                        to_table = right_match.group(1)
                        to_col = right_match.group(2)
                        
                        # Filter out self-references
                        if from_table != to_table:
                            relationships.append({
                                'from_table': from_table,
                                'from_column': from_col,
                                'to_table': to_table,
                                'to_column': to_col
                            })
        except Exception as e:
            print(f"Error processing view {view_name}: {e}")
    
    return relationships

def generate_dbml(conn, views_list):
    """
    Generate DBML format output by querying the database directly.
    """
    dbml_lines = []
    
    # Get all tables referenced in views
    print("Getting tables from views...")
    tables_in_views = get_tables_from_views(conn, views_list)
    
    if not tables_in_views:
        print("No tables found in views. Getting all user tables in the database...")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name 
            FROM sys.objects 
            WHERE type = 'U' 
            ORDER BY name
        """)
        tables_in_views = [row[0] for row in cursor.fetchall()]
    
    print(f"Found {len(tables_in_views)} tables")
    
    # Get relationships from foreign keys
    print("Getting foreign key relationships...")
    all_relationships = []
    seen_fks = set()
    for table_name in tables_in_views:
        relationships = get_table_foreign_keys(conn, table_name)
        for rel in relationships:
            # Only add relationship if BOTH tables are in tables_in_views
            if rel['from_table'] in tables_in_views and rel['to_table'] in tables_in_views:
                # Create a normalized key for deduplication
                from_key = (rel['from_table'], rel['from_column'])
                to_key = (rel['to_table'], rel['to_column'])
                
                # Normalize: always put the smaller table name first
                if from_key < to_key:
                    key = (from_key, to_key)
                else:
                    key = (to_key, from_key)
                
                if key not in seen_fks:
                    seen_fks.add(key)
                    all_relationships.append(rel)
    
    # Also get relationships from view definitions (fallback)
    print("Getting relationships from view definitions...")
    view_relationships = get_relationships_from_views(conn, views_list)
    seen = seen_fks.copy()
    for rel in view_relationships:
        # Only add relationship if BOTH tables are in tables_in_views
        if rel['from_table'] in tables_in_views and rel['to_table'] in tables_in_views:
            # Create a normalized key for deduplication
            from_key = (rel['from_table'], rel['from_column'])
            to_key = (rel['to_table'], rel['to_column'])
            
            # Normalize: always put the smaller table name first
            if from_key < to_key:
                key = (from_key, to_key)
            else:
                key = (to_key, from_key)
            
            if key not in seen:
                seen.add(key)
                all_relationships.append(rel)
    
    # Generate table definitions
    print("Generating table definitions...")
    for table_name in sorted(tables_in_views):
        # Get column information
        columns = get_table_columns(conn, table_name)
        
        # Table header
        dbml_lines.append(f'Table dbo.{table_name} {{')
        
        # Add columns
        for col in columns:
            col_name = col['name']
            col_type = col['type']
            
            # Skip computed columns that aren't referenced
            if col['is_computed'] and not col['is_primary_key']:
                continue
            
            # Determine if it's a primary key
            if col['is_primary_key']:
                dbml_lines.append(f'  {col_name} PK')
            else:
                dbml_lines.append(f'  {col_name} {col_type}')
        
        # Close table
        dbml_lines.append('}')
        dbml_lines.append('')
    
    # Add relationships with dbo. prefix
    if all_relationships:
        dbml_lines.append('// Relationships')
        dbml_lines.append('')
        
        # Deduplicate and format relationships
        seen = set()
        for rel in all_relationships:
            # Create a normalized key for deduplication
            from_key = (rel['from_table'], rel['from_column'])
            to_key = (rel['to_table'], rel['to_column'])
            
            # Normalize: always put the smaller table name first
            if from_key < to_key:
                key = (from_key, to_key)
            else:
                key = (to_key, from_key)
            
            if key in seen:
                continue
            seen.add(key)
            
            # Add dbo. prefix to all table names in references
            dbml_lines.append(f'Ref: dbo.{rel["from_table"]}.{rel["from_column"]} > dbo.{rel["to_table"]}.{rel["to_column"]}')
    
    return '\n'.join(dbml_lines)

def main():
    try:
        print("Connecting to database...")
        conn = get_db_connection()
        print("Connected successfully!")
        
        # Get all views
        print("Getting views from database...")
        views = get_all_views(conn)
        print(f"Found {len(views)} views")
        
        if not views:
            print("No views found in the database.")
            return
        
        # Generate DBML
        print("Generating DBML...")
        dbml_content = generate_dbml(conn, views)
        
        # Write output
        output_file = 'schema_diagram.dbml'
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(dbml_content)
        
        print(f"\n✅ DBML successfully written to {output_file}")
        
    except pyodbc.Error as e:
        print(f"Database connection error: {e}")
        print("\nPlease verify:")
        print("  - The server 'JARVIS' is accessible")
        print("  - The database 'kimDB' exists")
        print("  - You have appropriate permissions")
        print("  - The 'SQL Server' ODBC driver is installed")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
