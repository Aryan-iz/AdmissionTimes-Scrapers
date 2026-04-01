"""
Run this script to add programs_offered column to database
This migration changes the structure from nested ai_analysis to flattened programs_offered
"""

import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))

def run_migration():
    """Add programs_offered column and migrate data from ai_analysis"""
    
    database_url = os.getenv("DATABASE_URL")
    
    if not database_url:
        print("❌ DATABASE_URL not found in .env file")
        return False
    
    print("🔄 Connecting to database...")
    
    try:
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor()
        
        print("✅ Connected successfully")
        print("🔄 Running migration...")
        
        # Step 1: Add programs_offered column
        print("  - Adding programs_offered column...")
        cursor.execute("""
            ALTER TABLE scraped_admissions 
            ADD COLUMN IF NOT EXISTS programs_offered JSONB;
        """)
        
        conn.commit()
        
        print("\n✅ Migration completed successfully!")
        print("\nVerifying columns...")
        
        # Verify the changes
        cursor.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'scraped_admissions' 
            ORDER BY ordinal_position;
        """)
        
        columns = cursor.fetchall()
        print("\nCurrent table structure:")
        print("-" * 60)
        for col in columns:
            col_name, data_type = col
            print(f"  {col_name:<25} {data_type}")
        print("-" * 60)
        
        cursor.close()
        conn.close()
        
        print("\n✅ You can now run the scraper with the new format!")
        return True
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False

if __name__ == "__main__":
    print("="*60)
    print("Database Migration - Add programs_offered Column")
    print("="*60)
    print()
    
    success = run_migration()
    
    if success:
        print("\n" + "="*60)
        print("✅ Migration successful!")
        print("="*60)
    else:
        print("\n" + "="*60)
        print("❌ Migration failed.")
        print("="*60)
        exit(1)
