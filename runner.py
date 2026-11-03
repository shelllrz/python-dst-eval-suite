import unittest
import sys

def run_benchmark_suite() -> bool:
    print("==================================================")
    print("   INICIANDO SUÍTE DE BENCHMARK DE ENGENHARIA      ")
    print("==================================================")
    
    # Descobre e executa testes em arquivos que começam ou contêm lógica de teste
    # Como o cron_dst_scheduler.py já inclui seus próprios testes unitários:
    from cron_dst import CronExpressionTests, CronDstSchedulingTests
    
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(CronExpressionTests))
    suite.addTests(loader.loadTestsFromTestCase(CronDstSchedulingTests))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Retorna True se passou em tudo, False se houve falhas
    return result.wasSuccessful()

if __name__ == "__main__":
    success = run_benchmark_suite()
    sys.exit(0 if success else 1)