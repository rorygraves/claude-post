#!/usr/bin/env python3
"""Test runner for pagination functionality.

This script runs all pagination-related tests including unit tests and integration tests.
It provides detailed output and can be run independently of the main integration tests.
"""

import sys
import unittest
import asyncio
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

def run_pagination_tests():
    """Run all pagination tests and return results."""
    print("🧪 Running Pagination Test Suite")
    print("=" * 50)
    
    # Discover and run all pagination tests
    loader = unittest.TestLoader()
    
    # Load pagination unit tests
    try:
        from tests.test_pagination import (
            TestSearchCriteriaPagination,
            TestEmailClientPagination, 
            TestPaginationIntegration
        )
        
        pagination_suite = unittest.TestSuite()
        pagination_suite.addTests(loader.loadTestsFromTestCase(TestSearchCriteriaPagination))
        pagination_suite.addTests(loader.loadTestsFromTestCase(TestEmailClientPagination))
        pagination_suite.addTests(loader.loadTestsFromTestCase(TestPaginationIntegration))
        
        print("✅ Loaded pagination unit tests")
        
    except ImportError as e:
        print(f"❌ Failed to load pagination unit tests: {e}")
        return False
    
    # Load server pagination tests
    try:
        from tests.test_server_pagination import (
            TestMCPServerPagination,
            TestPaginationResponseFormatting
        )
        
        server_suite = unittest.TestSuite()
        server_suite.addTests(loader.loadTestsFromTestCase(TestMCPServerPagination))
        server_suite.addTests(loader.loadTestsFromTestCase(TestPaginationResponseFormatting))
        
        print("✅ Loaded server pagination tests")
        
    except ImportError as e:
        print(f"❌ Failed to load server pagination tests: {e}")
        return False
    
    # Combine all test suites
    all_tests = unittest.TestSuite([pagination_suite, server_suite])
    
    # Run tests with detailed output
    runner = unittest.TextTestRunner(
        verbosity=2,
        stream=sys.stdout,
        descriptions=True,
        failfast=False
    )
    
    print(f"\n🚀 Running {all_tests.countTestCases()} pagination tests...")
    print("-" * 50)
    
    result = runner.run(all_tests)
    
    # Print summary
    print("\n" + "=" * 50)
    print("📊 Test Summary")
    print("=" * 50)
    
    total_tests = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)
    passed = total_tests - failures - errors - skipped
    
    print(f"Total Tests: {total_tests}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failures}")
    print(f"💥 Errors: {errors}")
    print(f"⏭️  Skipped: {skipped}")
    
    success = failures == 0 and errors == 0
    
    if success:
        print("\n🎉 All pagination tests passed!")
    else:
        print("\n⚠️  Some pagination tests failed. See details above.")
        
        if result.failures:
            print("\nFailures:")
            for test, traceback in result.failures:
                print(f"- {test}: {traceback}")
                
        if result.errors:
            print("\nErrors:")
            for test, traceback in result.errors:
                print(f"- {test}: {traceback}")
    
    return success


def run_integration_test_sample():
    """Run a sample integration test to verify pagination works with real components."""
    print("\n🔄 Running Integration Test Sample")
    print("-" * 30)
    
    try:
        from src.email_client.email_client import SearchCriteria
        
        # Test SearchCriteria with pagination parameters
        print("Testing SearchCriteria validation...")
        
        # Test valid cases
        criteria1 = SearchCriteria(max_results=50, start_from=0)
        criteria2 = SearchCriteria(max_results=1, start_from=999)
        criteria3 = SearchCriteria(max_results=1000, start_from=0)
        print("✅ Valid pagination parameters accepted")
        
        # Test invalid cases
        invalid_cases = [
            ({"max_results": 0}, "max_results must be positive"),
            ({"max_results": -1}, "max_results must be positive"),
            ({"max_results": 1001}, "max_results cannot exceed 1000"),
            ({"start_from": -1}, "start_from must be non-negative"),
        ]
        
        for params, expected_error in invalid_cases:
            try:
                SearchCriteria(**params)
                print(f"❌ Should have failed for {params}")
                return False
            except ValueError as e:
                if expected_error in str(e):
                    print(f"✅ Correctly rejected {params}")
                else:
                    print(f"❌ Wrong error for {params}: {e}")
                    return False
        
        print("✅ All validation tests passed")
        return True
        
    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        return False


if __name__ == "__main__":
    print("🧪 ClaudePost Pagination Test Suite")
    print("=" * 60)
    
    # Run unit tests
    unit_test_success = run_pagination_tests()
    
    # Run integration test sample
    integration_success = run_integration_test_sample()
    
    # Overall result
    overall_success = unit_test_success and integration_success
    
    print("\n" + "=" * 60)
    if overall_success:
        print("🎉 ALL PAGINATION TESTS PASSED!")
        print("The pagination functionality is working correctly.")
        sys.exit(0)
    else:
        print("❌ SOME TESTS FAILED!")
        print("Please review the failures above and fix any issues.")
        sys.exit(1)