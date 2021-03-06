# Copyright 2015 MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import unicode_literals, absolute_import

import warnings

from pymongo.errors import InvalidOperation

from motor.motor_asyncio import (AsyncIOMotorClientSession,
                                 AsyncIOMotorGridFSBucket)
import test
from test import env
from test.asyncio_tests import asyncio_test, AsyncIOTestCase


class TestAsyncIOAwait(AsyncIOTestCase):
    @asyncio_test
    async def test_to_list(self):
        collection = self.collection
        await collection.delete_many({})

        results = await collection.find().sort('_id').to_list(length=None)
        self.assertEqual([], results)

        docs = [{'_id': 1}, {'_id': 2}]
        await collection.insert_many(docs)
        cursor = collection.find().sort('_id')
        results = await cursor.to_list(length=None)
        self.assertEqual(docs, results)
        results = await cursor.to_list(length=None)
        self.assertEqual([], results)

    @asyncio_test
    async def test_iter_cursor(self):
        collection = self.collection
        await collection.delete_many({})

        for n_docs in 0, 1, 2, 10:
            if n_docs:
                docs = [{'_id': i} for i in range(n_docs)]
                await collection.insert_many(docs)

            # Force extra batches to test iteration.
            j = 0
            async for doc in collection.find().sort('_id').batch_size(3):
                self.assertEqual(j, doc['_id'])
                j += 1

            self.assertEqual(j, n_docs)

            await collection.delete_many({})

    @asyncio_test
    async def test_iter_aggregate(self):
        collection = self.collection
        await collection.delete_many({})
        pipeline = [{'$sort': {'_id': 1}}]

        # Empty iterator.
        async for _ in collection.aggregate(pipeline):
            self.fail()

        for n_docs in 1, 2, 10:
            if n_docs:
                docs = [{'_id': i} for i in range(n_docs)]
                await collection.insert_many(docs)

            # Force extra batches to test iteration.
            j = 0
            async for doc in collection.aggregate(pipeline,
                                                  cursor={'batchSize': 3}):
                self.assertEqual(j, doc['_id'])
                j += 1

            self.assertEqual(j, n_docs)

            await collection.delete_many({})

    @asyncio_test
    async def test_iter_gridfs(self):
        gfs = AsyncIOMotorGridFSBucket(self.db)

        async def cleanup():
            await self.db.fs.files.delete_many({})
            await self.db.fs.chunks.delete_many({})

        await cleanup()

        # Empty iterator.
        async for _ in gfs.find({'_id': 1}):
            self.fail()

        data = b'data'

        for n_files in 1, 2, 10:
            for i in range(n_files):
                async with gfs.open_upload_stream(filename='filename') as f:
                    await f.write(data)

            # Force extra batches to test iteration.
            j = 0
            async for _ in gfs.find({'filename': 'filename'}).batch_size(3):
                j += 1

            self.assertEqual(j, n_files)
            await cleanup()

        await gfs.upload_from_stream_with_id(
            1, 'filename', source=data, chunk_size_bytes=1)
        cursor = gfs.find({'_id': 1})
        await cursor.fetch_next
        gout = cursor.next_object()
        chunks = []
        async for chunk in gout:
            chunks.append(chunk)

        self.assertEqual(len(chunks), len(data))
        self.assertEqual(b''.join(chunks), data)

    @asyncio_test
    async def test_stream_to_handler(self):
        # Sort of Tornado-specific, but it does work with asyncio.
        fs = AsyncIOMotorGridFSBucket(self.db)
        content_length = 1000
        await fs.delete(1)
        await fs.upload_from_stream_with_id(
            1, 'filename', source=b'a' * content_length)
        gridout = await fs.open_download_stream(1)
        handler = test.MockRequestHandler()
        await gridout.stream_to_handler(handler)
        self.assertEqual(content_length, handler.n_written)
        await fs.delete(1)

    @asyncio_test
    async def test_cursor_iter(self):
        # Have we handled the async iterator change in Python 3.5.2?:
        # python.org/dev/peps/pep-0492/#api-design-and-implementation-revisions
        with warnings.catch_warnings(record=True) as w:
            async for _ in self.collection.find():
                pass

        if w:
            self.fail(w[0].message)

    @asyncio_test
    async def test_list_indexes(self):
        await self.collection.drop()
        await self.collection.create_index([('x', 1)])
        await self.collection.create_index([('y', -1)])
        keys = set()
        async for info in self.collection.list_indexes():
            keys.add(info['name'])

        self.assertEqual(keys, {'_id_', 'x_1', 'y_-1'})

    @env.require_version_min(3, 6)
    @env.require_replica_set
    @asyncio_test
    async def test_session(self):
        s = self.cx.start_session()
        self.assertIsInstance(s, AsyncIOMotorClientSession)
        self.assertIs(s.client, self.cx)
        self.assertRaises(InvalidOperation, getattr, s, 'options')
        self.assertRaises(InvalidOperation, getattr, s, 'session_id')
        self.assertRaises(InvalidOperation, getattr, s, 'cluster_time')
        self.assertRaises(InvalidOperation, getattr, s, 'operation_time')
        self.assertRaises(InvalidOperation, getattr, s, 'has_ended')

        # Test that "await" converts start_session() into a session.
        retval = await s
        self.assertIs(retval, s)

        # We can access session properties now.
        s.options
        s.session_id
        s.cluster_time
        s.operation_time
        self.assertFalse(s.has_ended)
        await s.end_session()
        self.assertTrue(s.has_ended)

        async with self.cx.start_session() as s:
            self.assertIsInstance(s, AsyncIOMotorClientSession)
            self.assertFalse(s.has_ended)
            await s.end_session()
            self.assertTrue(s.has_ended)

        self.assertTrue(s.has_ended)

    @env.require_version_min(3, 7)
    @env.require_replica_set
    @asyncio_test
    async def test_transaction(self):
        async with self.cx.start_session() as s:
            s.start_transaction()
            self.assertTrue(s.delegate._in_transaction)
            self.assertFalse(s.has_ended)
            await s.end_session()
            self.assertFalse(s.delegate._in_transaction)
            self.assertTrue(s.has_ended)

        async with self.cx.start_session() as s:
            async with s.start_transaction():
                self.assertTrue(s.delegate._in_transaction)
                self.assertFalse(s.has_ended)
            self.assertFalse(s.delegate._in_transaction)
            self.assertFalse(s.has_ended)

        self.assertTrue(s.has_ended)
