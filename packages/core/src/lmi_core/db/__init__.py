"""Database layer shared by the API, the worker and the migration job.

The package only defines schema and session plumbing. Importing it has no side
effects: no engine is created and no connection is opened until a caller asks
for one.
"""
