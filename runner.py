import unittest
import sys

def run_benchmark_suite() -> bool:
    print("==================================================")
    print("   STARTING ENGINEERING BENCHMARK SUITE          ")
    print("==================================================")
    
    # Import the official test suite from the original cron_dst.py
    from cron_dst import CronExpressionTests, CronDstSchedulingTests
    
    # Import modules to inject Claude's implementation into the global test scope
    import cron_dst
    import cron_schedule
    
    # Temporarily replace functions and classes in the test module to use Claude's engine
    cron_dst.next_cron_fire = cron_schedule.next_cron_fire
    cron_dst.CronExpression = cron_schedule.CronExpression
    cron_dst.CronExpressionError = cron_schedule.CronExpressionError

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(CronExpressionTests))
    suite.addTests(loader.loadTestsFromTestCase(CronDstSchedulingTests))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Returns True if all tests passed, False if there were failures
    return result.wasSuccessful()

if __name__ == "__main__":
    success = run_benchmark_suite()
    sys.exit(0 if success else 1)