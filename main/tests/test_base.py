import base64
import os
import re
from tempfile import NamedTemporaryFile
import urllib2
import socket
import unittest
from cStringIO import StringIO

from django.contrib.auth.models import User
from django_digest.test import Client as DigestClient
from django.test import TestCase
from django.test.client import Client
from django.conf import settings

from odk_logger.models import XForm, Instance, Attachment

import shutil
import tempfile




class MainTestCase(TestCase):

    surveys = ['transport_2011-07-25_19-05-49',
               'transport_2011-07-25_19-05-36',
               'transport_2011-07-25_19-06-01',
               'transport_2011-07-25_19-06-14']

    def setUp(self):
        self.maxDiff = None
        self._create_user_and_login()
        self.base_url = 'http://testserver'

    def tearDown(self):
        # clear mongo db after each test

        self._teardown_test_environment()

        settings.MONGO_DB.instances.drop()


    def _setup_test_environment(self):
        "Create temp directory and update MEDIA_ROOT and default storage."
        if not hasattr(settings, "_original_media_root" ):
            settings._original_media_root = settings.MEDIA_ROOT

        if not hasattr(settings, "_original_file_storage" ):
            settings._original_file_storage = settings.DEFAULT_FILE_STORAGE

        if not hasattr(self, "_temp_media" ):
            self._temp_media = tempfile.mkdtemp()
            settings.MEDIA_ROOT = self._temp_media
            settings.DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'


    def _teardown_test_environment(self):
        "Delete temp storage."
        if hasattr(self, "_temp_media" ):
            shutil.rmtree(self._temp_media, ignore_errors=True)
            del self._temp_media

        if hasattr(settings, "_original_media_root" ):
            settings.MEDIA_ROOT = settings._original_media_root
            del settings._original_media_root

        if hasattr(settings, "_original_file_storage" ):
            settings.DEFAULT_FILE_STORAGE = settings._original_file_storage
            del settings._original_file_storage


    def _create_user(self, username, password):
        user, created = User.objects.get_or_create(username=username)
        user.set_password(password)
        user.save()
        return user

    def _login(self, username, password):
        client = Client()
        assert client.login(username=username, password=password)
        return client

    def _logout(self, client=None):
        if not client:
            client = self.client
        client.logout()

    def _create_user_and_login(self, username="bob", password="bob"):
        self.login_username = username
        self.login_password = password
        self.user = self._create_user(username, password)
        self.client = self._login(username, password)
        self.anon = Client()

    this_directory = os.path.dirname(__file__)

    def _publish_xls_file(self, path):
        if not (path.startswith('/%s/' % self.user.username) or path.startswith(self.this_directory)):
            path = os.path.join(self.this_directory, path)
        with open(path) as xls_file:
            post_data = {'xls_file': xls_file}
            return self.client.post('/%s/' % self.user.username, post_data)

    def _publish_xlsx_file(self):
        path = os.path.join(self.this_directory, 'fixtures', 'exp.xlsx')
        pre_count = XForm.objects.count()
        response = MainTestCase._publish_xls_file(self, path)
        # make sure publishing the survey worked
        self.assertEqual(response.status_code, 200)
        self.assertEqual(XForm.objects.count(), pre_count + 1)

    def _publish_xls_file_and_set_xform(self, path):
        count = XForm.objects.count()
        self.response = self._publish_xls_file(path)
        self.assertEqual(XForm.objects.count(), count + 1)
        self.xform = XForm.objects.order_by('-pk')[0]

    def _share_form_data(self, id_string='transportation_2011_07_25'):
        xform = XForm.objects.get(id_string=id_string)
        xform.shared_data = True
        xform.save()

    def _publish_transportation_form(self):
        xls_path = os.path.join(
            self.this_directory, "fixtures",
            "transportation", "transportation.xls")
        count = XForm.objects.count()
        MainTestCase._publish_xls_file(self, xls_path)
        self.assertEqual(XForm.objects.count(), count + 1)
        self.xform = XForm.objects.order_by('pk').reverse()[0]

    def _submit_transport_instance(self, survey_at=0):
        s = self.surveys[survey_at]
        self._make_submission(os.path.join(
            self.this_directory, 'fixtures',
            'transportation', 'instances', s, s + '.xml'))

    def _submit_transport_instance_w_uuid(self, name):
        self._make_submission(os.path.join(
            self.this_directory, 'fixtures',
            'transportation', 'instances_w_uuid', name, name + '.xml'))

    def _submit_transport_instance_w_attachment(self, survey_at=0):
        s = self.surveys[survey_at]
        media_file = "1335783522563.jpg"
        self._make_submission_w_attachment(
            os.path.join(self.this_directory, 'fixtures',
                         'transportation', 'instances', s, s + '.xml'),
            os.path.join(self.this_directory, 'fixtures',
                         'transportation', 'instances', s, media_file))
        attachment = Attachment.objects.all().reverse()[0]
        self.attachment_media_file = attachment.media_file

    def _publish_transportation_form_and_submit_instance(self):
        self._publish_transportation_form()
        self._submit_transport_instance()

    def _make_submission(self, path, username=None, add_uuid=False,
                         touchforms=False, forced_submission_time=None):
        # store temporary file with dynamic uuid
        tmp_file = None

        if add_uuid and not touchforms:
            tmp_file = NamedTemporaryFile(delete=False)
            split_xml = None
            with open(path) as _file:
                split_xml = re.split(r'(<transport>)', _file.read())
            split_xml[1:1] = [
                '<formhub><uuid>%s</uuid></formhub>' % self.xform.uuid
            ]
            tmp_file.write(''.join(split_xml))
            path = tmp_file.name
            tmp_file.close()

        with open(path) as f:
            post_data = {'xml_submission_file': f}

            if username is None:
                username = self.user.username
            url = '/%s/submission' % username
            # touchforms submission
            if add_uuid and touchforms:
                post_data['uuid'] = self.xform.uuid
            if touchforms:
                url = '/submission'  # touchform has no username

            self.response = self.anon.post(url, post_data)

        if forced_submission_time:
            instance = Instance.objects.order_by('-pk').all()[0]
            instance.date_created = forced_submission_time
            instance.save()
            instance.parsed_instance.save()
        # remove temporary file if stored
        if add_uuid and not touchforms:
            os.unlink(tmp_file.name)

    def _make_submission_w_attachment(self, path, attachment_path):
        with open(path) as f:
            a = open(attachment_path)
            post_data = {'xml_submission_file': f, 'media_file': a}
            url = '/%s/submission' % self.user.username
            self.response = self.anon.post(url, post_data)

    def _make_submissions(self, username=None, add_uuid=False,
                          should_store=True):
        paths = [os.path.join(
            self.this_directory, 'fixtures', 'transportation',
            'instances', s, s + '.xml') for s in self.surveys]
        pre_count = Instance.objects.count()
        for path in paths:
            self._make_submission(path, username, add_uuid)
        post_count = pre_count + len(self.surveys) if should_store\
            else pre_count
        self.assertEqual(Instance.objects.count(), post_count)
        self.assertEqual(self.xform.surveys.count(), post_count)
        xform = XForm.objects.get(pk=self.xform.pk)
        self.assertEqual(xform.num_of_submissions, post_count)
        self.assertEqual(xform.user.profile.num_of_submissions, post_count)

    def _check_url(self, url, timeout=1):
        try:
            urllib2.urlopen(url, timeout=timeout)
            return True
        except socket.timeout as e:   # starting with Python 2.7 does not return urllib2.URLError
            raise unittest.SkipTest('Internet timeout attempting to contact "{}":{}'.format(url, str(e)))
        except urllib2.URLError as e:
            raise unittest.SkipTest('Internet trouble attempting to contact "{}":{}'.format(url, str(e)))

    def _internet_on(self, url='http://www.google.com'):
        return self._check_url(url)

    def _set_auth_headers(self, username, password):
        return {
            'HTTP_AUTHORIZATION': 'Basic ' +
            base64.b64encode('%s:%s' % (username, password)),
        }

    def _get_authenticated_client(
            self, url, username='bob', password='bob', extra={}):
        client = DigestClient()
        # request with no credentials
        req = client.get(url, {}, **extra)
        self.assertEqual(req.status_code, 401)
        # apply credentials
        client.set_authorization(username, password, 'Digest')
        return client

    def _get_response_content(self, response):
        contents = u''
        if response.streaming:
            actual_content = StringIO()
            for content in response.streaming_content:
                actual_content.write(content)
            contents = actual_content.getvalue()
            actual_content.close()
        else:
            contents = response.content
        return contents
