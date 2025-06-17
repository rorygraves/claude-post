#!/usr/bin/env python3
"""
Test script demonstrating the data processing functionality.
This script simulates the email sender count example described in the design.
"""

import asyncio
import pandas as pd
import sys
import os

# Add the current directory to Python path to import local modules
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Import directly from the module path
from src.email_client.data_processing.datastore import DataStore

def demonstrate_data_processing():
    """Demonstrate the data processing workflow with sample email data."""
    
    # Create a DataStore instance
    datastore = DataStore()
    
    # Simulate email search results
    sample_emails = [
        {"id": "1", "from": "alice@example.com", "subject": "Meeting tomorrow", "date": "2024-01-15"},
        {"id": "2", "from": "bob@example.com", "subject": "Project update", "date": "2024-01-15"},
        {"id": "3", "from": "alice@example.com", "subject": "Follow up", "date": "2024-01-16"},
        {"id": "4", "from": "charlie@example.com", "subject": "New proposal", "date": "2024-01-16"},
        {"id": "5", "from": "alice@example.com", "subject": "Meeting notes", "date": "2024-01-17"},
        {"id": "6", "from": "bob@example.com", "subject": "Budget review", "date": "2024-01-17"},
        {"id": "7", "from": "alice@example.com", "subject": "Quick question", "date": "2024-01-18"},
    ]
    
    # Convert to DataFrame
    df = pd.DataFrame(sample_emails)
    
    print("=== Data Processing Demonstration ===\n")
    
    # Step 1: Create collection from search results
    metadata = datastore.create(df, "email_search_results")
    collection_id = metadata["id"]
    
    print(f"1. Created collection: {metadata['name']}")
    print(f"   ID: {collection_id}")
    print(f"   Shape: {metadata['shape']['rows']} rows, {metadata['shape']['columns']} columns")
    print(f"   Columns: {', '.join(metadata['columns'])}\n")
    
    # Step 2: Preview the data
    preview = datastore.preview(collection_id, rows=3)
    print("2. Preview of original data (first 3 rows):")
    for row in preview["preview"]:
        print(f"   {row}")
    print()
    
    # Step 3: Drop unnecessary columns
    operation1 = "df.drop(columns=['id', 'subject', 'date'])"
    result1 = datastore.update(collection_id, operation1)
    
    print(f"3. Applied operation: {operation1}")
    print(f"   New shape: {result1['shape']['rows']} rows, {result1['shape']['columns']} columns")
    print(f"   Columns: {', '.join(result1['columns'])}\n")
    
    # Step 4: Group by sender and count
    operation2 = "df.groupby('from').size().reset_index(name='count').sort_values('count', ascending=False)"
    result2 = datastore.update(collection_id, operation2)
    
    print(f"4. Applied operation: {operation2}")
    print(f"   New shape: {result2['shape']['rows']} rows, {result2['shape']['columns']} columns")
    print(f"   Columns: {', '.join(result2['columns'])}\n")
    
    # Step 5: Fetch final results
    final_results = datastore.fetch(collection_id, format="records")
    
    print("5. Final results - Email sender counts:")
    print("   Sender | Count")
    print("   " + "-" * 30)
    for row in final_results["data"]:
        print(f"   {row['from']} | {row['count']}")
    print()
    
    # Step 6: Show collection metadata
    collections = datastore.list_collections()
    print("6. Available collections:")
    for coll in collections:
        print(f"   - {coll['name']} (ID: {coll['id'][:8]}...)")
    
    # Clean up
    datastore.delete(collection_id)
    print(f"\n7. Deleted collection {collection_id[:8]}...")


if __name__ == "__main__":
    demonstrate_data_processing()