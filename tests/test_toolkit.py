# -*- coding: utf-8 -*-
import unittest

from docker_registry import toolkit


class TestToolkit(unittest.TestCase):

    def test_resolve_repository_name_good(self):
        repositories = [
            {
                'name': 'test',
                'expected_hostname': toolkit.public_index_url(),
                'expected_image': 'test'
            },
            {
                'name': 'testy/test',
                'expected_hostname': toolkit.public_index_url(),
                'expected_image': 'testy/test'
            },
            {
                'name': 'example.com/test',
                'expected_hostname': 'example.com',
                'expected_image': 'test'
            },
            {
                'name': 'example.com/testy/test',
                'expected_hostname': 'example.com',
                'expected_image': 'testy/test'
            },
            {
                'name': 'example.com:5000/test',
                'expected_hostname': 'example.com:5000',
                'expected_image': 'test'
            },
            {
                'name': 'example.com:5000/testy/test',
                'expected_hostname': 'example.com:5000',
                'expected_image': 'testy/test'
            },
            {
                'name': 'http://example.com/test',
                'expected_hostname': 'http://example.com',
                'expected_image': 'test'
            },
            {
                'name': 'http://example.com/testy/test',
                'expected_hostname': 'http://example.com',
                'expected_image': 'testy/test'
            },
            {
                'name': 'http://example.com:5000/test',
                'expected_hostname': 'http://example.com:5000',
                'expected_image': 'test'
            },
            {
                'name': 'http://example.com:5000/testy/test',
                'expected_hostname': 'http://example.com:5000',
                'expected_image': 'testy/test'
            },
            {
                'name': 'https://example.com/test',
                'expected_hostname': 'https://example.com',
                'expected_image': 'test'
            },
            {
                'name': 'https://example.com/testy/test',
                'expected_hostname': 'https://example.com',
                'expected_image': 'testy/test'
            },
            {
                'name': 'https://example.com:5000/test',
                'expected_hostname': 'https://example.com:5000',
                'expected_image': 'test'
            },
            {
                'name': 'https://example.com:5000/testy/test',
                'expected_hostname': 'https://example.com:5000',
                'expected_image': 'testy/test'
            },
        ]

        for repo in repositories:
            hostname, image = toolkit.resolve_repository_name(repo['name'])
            self.assertEqual(hostname, repo['expected_hostname'])
            self.assertEqual(image, repo['expected_image'])
