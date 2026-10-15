"""Synthetic company profile; public edition contains no real identity or seal."""
import re

SG_BUSINESS_NUMBER = '0000000000'
SG_NAMES = {'그로스 일렉트릭', '그로스 일렉트릭'}
SG_PROFILE_VERSION = 2
SG_DEFAULTS = {
    'name': '그로스 일렉트릭',
    'businessNumber': '000-00-00000',
    'representative': '김데모',
    # Intentionally invalid demonstration identity.
    'address': '가상시 데모구 그로스길 10',
    'businessType': '제조,도소매',
    'businessItem': '전기콘트롤박스',
    'phone': '010-0000-0000',
    'fax': '02-000-0000',
    'email': 'hello@growth.example',
}
SG_PREVIOUS = {
    'name': '그로스 일렉트릭',
    'address': '가상시 샘플구 예시길 20',
    'businessType': '도매 및 소매업, 통신판매업',
    'businessItem': '전기용 기계·장비 및 관련 기자재 도매업',
}


def is_sg(company):
    number = re.sub(r'\D', '', company.get('businessNumber', ''))
    name = re.sub(r'\s', '', company.get('name', ''))
    return number == SG_BUSINESS_NUMBER or (not number and name in {re.sub(r'\s', '', n) for n in SG_NAMES})


def initialize_company(company):
    """Ship the approved profile outside transaction data; retain later custom edits."""
    if is_sg(company):
        if company.get('sgProfileVersion', 0) < SG_PROFILE_VERSION:
            for key, previous in SG_PREVIOUS.items():
                if company.get(key) == previous:
                    company[key] = SG_DEFAULTS[key]
        for key, value in SG_DEFAULTS.items():
            company.setdefault(key, value)
        company['sgProfileVersion'] = SG_PROFILE_VERSION
    return company


def use_sg_seal(company):
    # Public demo deliberately ships no business seal.
    return False
