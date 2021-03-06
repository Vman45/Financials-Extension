#  baseclient.py
#
#  license: GNU LGPL
#
#  This library is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.


import codecs
import gzip
import logging
import os
import pathlib
import random
import select

from http.client import HTTPConnection, HTTPSConnection, HTTPException
from http import cookiejar

import urllib.request

from datacode import Datacode

logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)


class RedirectException(HTTPException):
    def __init__(self, location):
        self.location = location


class HttpException(HTTPException):
    def __init__(self, url, status):
        self.url = url
        self.status = status


class BaseClient:
    def __init__(self):
        self.connections = {}
        self.cookies = cookiejar.CookieJar()
        self.last_url = None

        self.basedir = os.path.join(str(pathlib.Path.home()), '.financials-extension')
        os.makedirs(self.basedir, exist_ok=True)

        user_agents = [
            'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:74.0) Gecko/20100101 Firefox/74.0',
            'Mozilla/5.0 (Windows NT 6.3; Win64; x64; rv:74.0) Gecko/20100101 Firefox/74.0',
            'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:75.0) Gecko/20100101 Firefox/75.0',
            'Mozilla/5.0 (Windows NT 6.3; Win64; x64; rv:75.0) Gecko/20100101 Firefox/75.0',
            'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:76.0) Gecko/20100101 Firefox/76.0',
            'Mozilla/5.0 (Windows NT 6.3; Win64; x64; rv:76.0) Gecko/20100101 Firefox/76.0',
            'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:77.0) Gecko/20100101 Firefox/77.0',
            'Mozilla/5.0 (Windows NT 6.3; Win64; x64; rv:77.0) Gecko/20100101 Firefox/77.0',

            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.149 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.108 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.106 Safari/537.36'
        ]

        self.default_headers = {
            'User-Agent': random.sample(user_agents, 1)[0],
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0'
        }

        self.response = None

    def request(self, method: str, url: str, data=None, headers={}, cookies=[], **kwargs):

        _headers = self.default_headers.copy()
        if headers:
            for key, value in headers.items():
                _headers[key] = value

        if cookies:
            for c in cookies:
                self.cookies.set_cookie(c)

        connection = None

        scheme, _, host, path = url.split('/', 3)

        if (scheme, host) in self.connections:
            connection = self.connections.get((scheme, host))

        if connection and select.select([connection.sock], [], [], 0)[0]:
            connection.close()
            connection = None

        if not connection:
            logger.debug('Creating connection --------------------------------------------------')
            connection = HTTPConnection(host, **kwargs) if scheme == 'http:' else HTTPSConnection(host, **kwargs)

        logger.debug('Creating request -----------------------------------------------------')
        logger.info('url=%s', url)

        self.last_url = url

        # generate and add cookie headers
        request = urllib.request.Request(url)

        self.cookies.add_cookie_header(request)
        if request.get_header('Cookie'):
            _headers['Cookie'] = request.get_header('Cookie')

        for key, value in _headers.items():
            logger.debug('Header: %s=%s', key, value)

        # request
        connection.request(method, '/' + path, data, _headers)
        response = connection.getresponse()

        logger.debug('Processing response --------------------------------------------------')

        # logger.debug('response.status={}'.format(response.status))
        for key, value in response.getheaders():
            logger.debug('Header: %s=%s', key, value)

        self.cookies.extract_cookies(response, request)
        self.connections[(scheme, host)] = connection

        return response

    def urlopen(self, url, redirect=True, data=None, headers={}, cookies=[], **kwargs):

        self.response = self.request('POST' if data else 'GET', url, data, headers, cookies, **kwargs)
        text = self.response.read()

        # Allow two redirects: used by Yahoo for some cookie based consent

        if 300 <= self.response.status < 400:
            location = self.response.getheader('Location')

            if location and redirect:

                if location.startswith('/'):
                    scheme, _, host, path = url.split('/', 3)
                    location = '{}//{}{}'.format(scheme, host, location)

                self.response = self.request('POST' if data else 'GET', location, data, headers, cookies, **kwargs)
                text = self.response.read()

                if 300 <= self.response.status < 400:
                    location = self.response.getheader('Location')

                    if location and redirect:

                        if location.startswith('/'):
                            scheme, _, host, path = url.split('/', 3)
                            location = '{}//{}{}'.format(scheme, host, location)

                        self.response = self.request('POST' if data else 'GET', location, data, headers, cookies, **kwargs)
                        text = self.response.read()
                    else:
                        raise RedirectException(location)

            else:
                raise RedirectException(location)

        if self.response.status >= 400:
            raise HttpException(url, self.response.status)

        if self.response.getheader('Content-Encoding') == 'gzip':
            text = gzip.decompress(text)

        content_type = self.response.headers.get_content_charset()
        if content_type is None:
            content_type = 'utf-8'
        text = codecs.decode(text, encoding=content_type, errors='ignore')

        return text

    def _return_value(self, data: dict, datacode: int):

        """
        Format data from tick data to out put format - mostly formatting date/time

        :param data: tick data
        :param datacode: the requested datacode
        :return: value or None
        """

        if data is None:
            return None

        try:
            if datacode == Datacode.PREV_CLOSE.value and Datacode.PREV_CLOSE in data:
                return data[Datacode.PREV_CLOSE]

            elif datacode == Datacode.OPEN.value and Datacode.OPEN in data:
                return data[Datacode.OPEN]

            elif datacode == Datacode.CHANGE.value and Datacode.CHANGE in data:
                return data[Datacode.CHANGE]

            elif datacode == Datacode.LAST_PRICE_DATE.value and Datacode.LAST_PRICE_DATE in data:
                if data[Datacode.LAST_PRICE_DATE]:
                    return data[Datacode.LAST_PRICE_DATE].isoformat()
                else:
                    return data[Datacode.LAST_PRICE_DATE]

            elif datacode == Datacode.LAST_PRICE_TIME.value and Datacode.LAST_PRICE_TIME in data:
                if data[Datacode.LAST_PRICE_TIME]:
                    return data[Datacode.LAST_PRICE_TIME].isoformat()
                else:
                    return data[Datacode.LAST_PRICE_TIME]

            elif datacode == Datacode.CHANGE_IN_PERCENT.value and Datacode.CHANGE_IN_PERCENT in data:
                return data[Datacode.CHANGE_IN_PERCENT]

            elif datacode == Datacode.LOW.value and Datacode.LOW in data:
                return data[Datacode.LOW]

            elif datacode == Datacode.HIGH.value and Datacode.HIGH in data:
                return data[Datacode.HIGH]

            elif datacode == Datacode.LAST_PRICE.value and Datacode.LAST_PRICE in data:
                return data[Datacode.LAST_PRICE]

            elif datacode == Datacode.LOW_52_WEEK.value and Datacode.LOW_52_WEEK in data:
                return data[Datacode.LOW_52_WEEK]

            elif datacode == Datacode.HIGH_52_WEEK.value and Datacode.HIGH_52_WEEK in data:
                return data[Datacode.HIGH_52_WEEK]

            elif datacode == Datacode.MARKET_CAP.value and Datacode.MARKET_CAP in data:
                return data[Datacode.MARKET_CAP]

            elif datacode == Datacode.VOLUME.value and Datacode.VOLUME in data:
                return data[Datacode.VOLUME]

            elif datacode == Datacode.AVG_DAILY_VOL_3MOMTH.value and Datacode.AVG_DAILY_VOL_3MOMTH in data:
                return data[Datacode.AVG_DAILY_VOL_3MOMTH]

            elif datacode == Datacode.CLOSE.value and Datacode.CLOSE in data:
                return data[Datacode.CLOSE]

            elif datacode == Datacode.ADJ_CLOSE.value and Datacode.ADJ_CLOSE in data:
                return data[Datacode.ADJ_CLOSE]

            elif datacode == Datacode.SECTOR.value and Datacode.SECTOR in data:
                return data[Datacode.SECTOR]

            elif datacode == Datacode.INDUSTRY.value and Datacode.INDUSTRY in data:
                return data[Datacode.INDUSTRY]

            elif datacode == Datacode.TICKER.value and Datacode.TICKER in data:
                return data[Datacode.TICKER]

            elif datacode == Datacode.EXCHANGE.value and Datacode.EXCHANGE in data:
                return data[Datacode.EXCHANGE]

            elif datacode == Datacode.CURRENCY.value and Datacode.CURRENCY in data:
                return data[Datacode.CURRENCY]

            elif datacode == Datacode.NAME.value and Datacode.NAME in data:
                return data[Datacode.NAME]

            elif datacode == Datacode.TIMEZONE.value and Datacode.TIMEZONE in data:
                if data[Datacode.TIMEZONE] is not None and type(data[Datacode.TIMEZONE]) != str:
                    return str(data[Datacode.TIMEZONE])
                else:
                    return data[Datacode.TIMEZONE]

        except BaseException as e:
            return 'BaseClient.return_value(\'{}\', {}) - {}'.format(data, datacode, e)

        return "Data doesn't exist - {}".format(datacode)

    def save_wrapper(self, f):
        try:
            value = f()
            logger.debug(value)
            return value
        except BaseException as e:
            pass

        return None
