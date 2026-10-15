"""Durable built-in templates: never stored in resettable business documents."""
import copy
from company_profile import initialize_company, SG_DEFAULTS

TEMPLATE_VERSION = 7


def export_template(kind, fmt, settings=None):
    if kind not in ('quote', 'statement') or fmt not in ('pdf', 'xlsx'):
        raise ValueError('견적서 또는 거래명세서의 PDF·XLSX 양식을 선택해 주세요.')
    import workflow
    from erp_documents import render_xlsx
    from pdf_documents import render_pdf
    company=copy.deepcopy(settings if settings is not None else SG_DEFAULTS)
    initialize_company(company)
    record=dict(id='template-'+kind, number='', version=1, date='', title='',
                isTemplate=True, documentDateLabel='작성일', orderNumber='',
                company=workflow.profile(company), customer={'name':''},
                net=0,vat=0,notes='',attention='',deliveryTerms='',paymentTerms='',
                items=[dict(name='',spec='',section='',unit='',quantity=0,unitPrice=0,net=0,vat=0) for _ in range(6)])
    content=(render_xlsx('quote' if kind=='quote' else 'voucher',record) if fmt=='xlsx'
             else render_pdf('quote' if kind=='quote' else 'delivery',record))
    label='견적서' if kind=='quote' else '거래명세서'
    return f'{label}-기본양식.{fmt}',content
