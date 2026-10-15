import copy
import unittest

import ledger
import server
import workflow


class TaskBulkTests(unittest.TestCase):
 def setUp(self):
  self.state=server.empty_state()
  self.state['accounts']=[dict(id='customer',name='노바 일렉트로')]
  workflow.normalize(self.state)
  self.run('erp.initialize',confirmed=True)

 def run(self,action=None,**data):
  if action is None or not isinstance(action,str):return super().run(action)
  result,content=workflow.apply(self.state,dict(action=action,data=data),'demo')
  server.validate_state(self.state)
  return result,content

 def test_manual_tasks_support_atomic_bulk_completion_and_deletion(self):
  fields=dict(accountId='customer',date='2026-10-05')
  first,_=self.run('erp.task.save',title='첫 번째 할 일',**fields)
  second,_=self.run('erp.task.save',title='두 번째 할 일',**fields)
  third,_=self.run('erp.task.save',title='남길 할 일',**fields)
  ids=[first['recordId'],second['recordId']]

  result,_=self.run('erp.task.bulk',ids=ids,operation='complete')
  self.assertEqual(result['count'],2)
  for task_id in ids:
   task=ledger.get(self.state,'tasks',task_id)
   self.assertEqual(task['status'],'completed')
   self.assertTrue(task['done'])

  result,_=self.run('erp.task.bulk',ids=ids,operation='delete')
  self.assertEqual(result['recordIds'],ids)
  self.assertEqual([task['id'] for task in self.state['tasks'] if task['id'] in ids],[])
  self.assertIsNotNone(ledger.get(self.state,'tasks',third['recordId']))

  before=copy.deepcopy(self.state)
  with self.assertRaises(ValueError):
   self.run('erp.task.bulk',ids=[third['recordId'],'missing-task'],operation='delete')
  self.assertEqual(self.state,before)


if __name__=='__main__':unittest.main()
