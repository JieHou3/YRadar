from abc import ABC, abstractmethod


class BaseFetcher(ABC):
    def __init__(self):
        pass

    @abstractmethod
    def fetch(self):
        """
        Fetch and parse listings. Return a list of normalized vehicle dicts:
        [
            {
                'vin': str,
                'year': int,
                'model': str,
                'trim': str,
                'price': float,
                'mileage': int,
                'url': str,
                'dealer_name': str,
                'dealer_address': str,
                'source': str
            },
            ...
        ]
        """
        raise NotImplementedError("Subclasses must implement fetch()")
