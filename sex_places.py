import csv
import lzma
import math
import os
import re
import sys
from configparser import RawConfigParser
from datetime import date
from itertools import chain
from urllib.parse import parse_qs, urlencode, urlparse

import pandas as pd
import requests
from geopy.distance import vincenty

DATE = date.today().strftime('%Y-%m-%d')
DATA_DIR = 'data'
OUTPUT = os.path.join(DATA_DIR, '{}-sex-place-distances.xz'.format(DATE))

settings = RawConfigParser()
settings.read('config.ini')


class SexPlacesNearBy:

    BASE_URL = 'https://maps.googleapis.com/maps/api/place/'
    KEYWORDS = ('acompanhantes',
                'adult entertainment club',
                'adult entertainment store',
                'gay sauna',
                'massagem',
                'modeling agency',
                'motel',
                'night club',
                'sex club',
                'sex shop',
                'strip club',
                'swinger clubs')

    def __init__(self, company, key=None):
        """
        :param company: (dict) Company with name, cnpj, latitude and longitude
        :param key: (str) Google Places API key
        """
        self.company = company
        self.key = key or settings.get('Google', 'APIKey')
        self.latitude = self.company['latitude']
        self.longitude = self.company['longitude']

    def get_closest(self):
        """Set self.closest to the closest (and relevant) place found"""
        if not self.is_valid():
            return None

        places = (self.load_place(k) for k in self.KEYWORDS)
        cleaned = (p for p in places if p and p.get('distance'))
        ordered = sorted(cleaned, key=lambda x: x['distance'])

        for place in ordered:
            place = self.load_details(place)
            name = place.get('name', '').lower()
            if place['keyword'] == 'motel' and 'hotel' in name:
                pass  # google returns hotels when looking for a motel
            else:
                return place

        return None

    @staticmethod
    def is_valid_coordinate(coord):
        try:
            as_float = float(coord)
        except ValueError:
            return False

        return False if math.isnan(as_float) else True

    def is_valid(self):
        coords = (self.latitude, self.longitude)
        if not all(map(self.is_valid_coordinate, coords)):
            msg = 'No geolocation information for company: {} ({})'
            print(msg.format(get_name(self.company), self.company['cnpj']))
            return False

        return True

    def get(self, keyword):
        msg = 'Looking for a {keyword} near {name} ({cnpj})…'
        print(msg.format(keyword=keyword, **self.company))
        return requests.get(self.nearby_url(keyword))

    def parse(self, response):
        """
        Return a dictonary containt information of the nearest sex place
        around a given company.
        :param response: HTTP response generated by requests
        :return: (dict) with
            * name : The name of nearest sex place
            * latitude : The latitude of nearest sex place
            * longitude : The longitude of nearest sex place
            * distance : Distance (in meters) between the company and the
              nearest sex place
            * address : The address of the nearest sex place
            * phone : The phone of the nearest sex place
            * id : The Google Place ID of the nearest sex place
            * keyword : term that matched the sex place in Google Place Search
        """
        url = response.url
        response = response.json()

        if response['status'] != 'OK':
            if 'error_message' in response:
                status, error = response.get('status'), response.get('error')
                print('{}: {}'.format(status, error))
            elif response.get('status') != 'ZERO_RESULTS':
                msg = 'Google Places API Status: {}'
                print(msg.format(response.get('status')))

        else:
            place = response.get('results', [{}])[0]

            location = place.get('geometry', {}).get('location', {})
            latitude = float(location.get('lat'))
            longitude = float(location.get('lng'))

            company_location = (self.latitude, self.longitude)
            place_location = (latitude, longitude)
            distance = vincenty(company_location, place_location)

            return {
                'id': place.get('place_id'),
                'keyword': self.keyword_from_url(url),
                'latitude': latitude,
                'longitude': longitude,
                'distance': distance.meters,
                'cnpj': re.sub(r'\D', '', self.company.get('cnpj')),
                'company_name': self.company.get('name'),
                'company_trade_name': self.company.get('trade_name')
            }

    def load_details(self, place):
        """
        :param place: dictonary with id key.
        :return: dictionary updated with name, address and phone.
        """
        prefix = '💋 ' if place['distance'] < 5 else ''
        msg = '{}Found something interesting {:.2f}m away from {}…'
        print(msg.format(prefix, place['distance'], get_name(self.company)))

        place_id = place.get('id')
        if not place_id:
            return place

        details = requests.get(self.details_url(place_id)).json()
        if not details:
            return place

        result = details.get('result', {})
        place.update(dict(
            name=result.get('name', ''),
            address=result.get('formatted_address', ''),
            phone=result.get('formatted_phone_number', '')
        ))
        return place

    def load_place(self, keyword):
        """
        Given a keyword it loads the place returned by the API to self.places.
        """
        response = self.get(keyword)
        return self.parse(response)

    def details_url(self, place):
        """
        :param place: (int or str) ID of the place in Google Place
        :return: (str) URL to do a place details Google Places search
        """
        query = (('placeid', place),)
        return self.google_places_url('details', query)

    def nearby_url(self, keyword):
        """
        :param keywork: (str) category to search places
        :return: (str) URL to do a nearby Google Places search
        """
        location = '{},{}'.format(self.latitude, self.longitude)
        query = (
            ('location', location),
            ('keyword', keyword),
            ('rankby', 'distance'),
        )
        return self.google_places_url('nearbysearch', query)

    def google_places_url(self, endpoint, query=None, format='json'):
        """
        :param endpoint: (str) Google Places API endpoint name (e.g. details)
        :param query: (tuple) tuples with key/values pairs for the URL query
        :param format: (str) output format (default is `json`)
        :return: (str) URL to do an authenticated Google Places request
        """
        key = ('key', self.key)
        query = tuple(chain(query, (key,))) if query else (key)
        parts = (
            self.BASE_URL,
            endpoint,
            '/{}?'.format(format),
            urlencode(query)
        )
        return ''.join(parts)

    def keyword_from_url(self, url):
        """Given a URL it returns the keyword used in the query."""
        qs = parse_qs(urlparse(url).query)
        try:
            keyword = qs.get('keyword')
            return keyword[0]
        except TypeError:
            return None


def main(companies):
    """
    :param comanies: (Pandas DataFrame)
    """
    for company_row in companies.itertuples(index=True):
        company = dict(company_row._asdict())  # _asdict returns an OrderedDict
        sex_places = SexPlacesNearBy(company)
        write_to_csv(OUTPUT, sex_places.get_closest())


def write_to_csv(path, place=None, **kwargs):
    """
    Receives a given place (dict) and writes it in the CSV format into path.
    CSV headers are defined in `fieldnames`. The named argument `headers`
    (bool) determines if thie functions write the CSV header or not.
    """
    print('Writing {}'.format(place))
    headers = kwargs.get('headers', False)
    if not place and not headers:
        return

    fieldnames = (
        'id', 'keyword', 'latitude', 'longitude', 'distance', 'name',
        'address', 'phone', 'cnpj', 'company_name', 'company_trade_name'
    )

    with lzma.open(path, 'at') as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        if headers:
            writer.writeheader()
        if place:
            contents = {k: v for k, v in place.items() if k in fieldnames}
            writer.writerow(contents)


def find_newest_file(name):
    """
    Assuming that the files will be in the form of :
    yyyy-mm-dd-type_of_file.xz we can try to find the newest file
    based on the date, but if the file doesn't exist fallback to another
    date until all dates are exhausted
    """
    date_regex = re.compile('\d{4}-\d{2}-\d{2}')

    matches = (date_regex.findall(f) for f in os.listdir(DATA_DIR))
    dates = sorted(set([l[0] for l in matches if l]), reverse=True)
    for d in dates:
        filename = os.path.join(DATA_DIR, '{}-{}.xz'.format(d, name))
        if os.path.isfile(filename):
            return filename

    return None


def get_name(company):
    trade_name = company.get('trade_name')
    if trade_name:
        return trade_name
    return company.get('name')


if __name__ == '__main__':
    print('Loading companies dataset…')
    usecols = ('cnpj', 'trade_name', 'name', 'latitude', 'longitude')
    companies_path = find_newest_file('companies')
    companies = pd.read_csv(companies_path, usecols=usecols, low_memory=False)
    companies = companies.fillna(value='')

    try:
        sample = int(sys.argv[1])
    except (IndexError, ValueError):
        sample = None

    if sample:
        main(companies.sample(sample))
    else:
        main(companies)