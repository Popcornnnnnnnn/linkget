"""Keep bounded acquisition metadata separate from untrusted page text."""
import re


def browser_metadata(page):
    controller = page.get('browser_controller')
    if not isinstance(controller, dict) or controller.get('kind') != 'browseros_neo_mcp':
        return {}
    if (controller.get('reader_revision') != 1 or
            controller.get('credentials_exported') is not False or
            controller.get('temporary_tab_cleanup') not in ('closed', 'preserved_navigated_tab')):
        raise ValueError('Neo content requires bounded controller and cleanup evidence.')
    version = controller.get('server_version')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9][0-9A-Za-z.+-]{0,63}', version):
        raise ValueError('Neo content requires a bounded server version.')
    return {'reader_backend': 'browseros_neo',
            'browser_controller': {key: controller[key] for key in
                ('kind', 'reader_revision', 'temporary_tab_cleanup', 'credentials_exported', 'server_version')}}
