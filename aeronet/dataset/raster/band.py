import os
import numpy as np

import rasterio
from rasterio import Affine
from rasterio.crs import CRS
from rasterio.coords import BoundingBox
from rasterio.warp import calculate_default_transform, reproject, Resampling

from ..coords import get_utm_zone
from .geoobject import GeoObject
from ._utils import band_shape_guard, random_name

TMP_DIR = '/tmp/raster'


class Band(GeoObject):
    """
    Hard drive object `Band` - Rasterio Band wrapper
    """
    def __init__(self, fp):
        """
        Args:
            fp: path to GeoTiff file
        """
        super().__init__()
        self._band = rasterio.open(fp)
        self._tmp_file = False

    def __del__(self):
        fp = self._band.name
        self._band.close()
        if self._tmp_file:
            os.remove(fp)

    # ======================== PROPERTY BLOCK ========================
    @property
    def crs(self):
        return self._band.crs

    @property
    def transform(self):
        return self._band.transform

    @property
    def nodata(self):
        return self._band.nodata

    @property
    def res(self):
        return self._band.res

    @property
    def width(self):
        return self._band.width

    @property
    def height(self):
        return self._band.height

    @property
    def count(self):
        return self._band.count

    @property
    def shape(self):
        return self.height, self.width

    @property
    def name(self):
        """Name of file without extension."""
        return os.path.basename(self._band.name).split('.')[0]

    @property
    def bounds(self):
        return self._band.bounds

    @property
    def meta(self):
        return self._band.meta

    @property
    def dtype(self):
        """Raster type of data."""
        return self._band.dtypes[0]

    # ======================== METHODS BLOCK ========================

    def numpy(self):
        return self.sample(0, 0, self.height, self.width).numpy()

    def same(self, other):
        """Compare bands by crs, transform, width, height. If all match return True."""
        res = True
        res = res and (self.crs == other.crs)
        res = res and (self.transform == other.transform)
        res = res and (self.height == other.height)
        res = res and (self.width == other.width)
        return res

    def sample(self, y, x, height, width, **kwargs):
        """
        Read sample of of band to memory with specified:
            x, y - pixel coordinates of left top corner
            width, height - spatial dimension of sample in pixels
        Return: `Sample` object
        """

        coord_x = self.transform.c + x * self.transform.a
        coord_y = self.transform.f + y * self.transform.e

        dst_crs = self.crs
        dst_name = os.path.basename(self.name)
        dst_nodata = self.nodata if self.nodata is not None else 0
        dst_transform = Affine(self.transform.a, self.transform.b, coord_x,
                               self.transform.d, self.transform.e, coord_y)

        dst_raster = self._band.read(window=((y, y + height), (x, x + width)),
                                     boundless=True, fill_value=dst_nodata)

        sample = BandSample(dst_name, dst_raster, dst_crs, dst_transform, dst_nodata)

        return sample

    def resample(self, dst_res, fp=None, interpolation='nearest'):
        """
            Changes the spatial resolution the Band to specified crs, The result is
            Args:
                dst_res: new resolution, in projection units
                fp: file to save the new reprojected band. If None, a new temporary file is created
                interpolation: type of the raster interpolation. 'nearest', 'bilinear', 'cubic' or other supported by GDAL

            Returns:
                new Band object pointing to the fp
            """
        # get temporary filepath if such is not provided
        tmp_file = False if fp is not None else True
        if fp is None:
            fp = '{tmp}/resampled/{directory}/{name}.tif'.format(
                tmp=TMP_DIR, directory=random_name(), name=self.name)

        os.makedirs(os.path.dirname(fp), exist_ok=True)

        transform = Affine(dst_res[0], self.transform.b, self.transform.c,
                           self.transform.d, - dst_res[1], self.transform.f)
        width = round(self.width / (dst_res[0]/self.res[0]))
        height = round(self.height / (dst_res[1]/self.res[1]))

        kwargs = self.meta.copy()
        kwargs.update({
            'transform': transform,
            'width': width,
            'height': height
        })

        with rasterio.open(fp, 'w', **kwargs) as dst:
                reproject(
                    source=rasterio.band(self._band, 1),
                    destination=rasterio.band(dst, 1),
                    src_transform=self.transform,
                    src_crs=self.crs,
                    dst_transform=transform,
                    dst_crs=self.crs,
                    resampling=getattr(Resampling, interpolation))

        # new band
        band = Band(fp)
        band._tmp_file = tmp_file # file will be automatically removed when `Band` instance will be deleted

        return band

    def reproject(self, dst_crs, fp=None, interpolation='nearest'):
        """
        Reprojects the Band to specified crs
        Args:
            dst_crs: new crs, either in the rasterio.CRS format, or in interpretable string, \
            or 'utm' for automatic selection of the utm zone
            fp: file to save the new reprojected band. If None, a new temporary file is created
            interpolation: type of the raster interpolation. 'nearest', 'bilinear', 'cubic' or other supported by GDAL

        Returns:
            new Band object pointing to the fp
        """
        if isinstance(dst_crs, str) and dst_crs == 'utm':
            dst_crs = get_utm_zone(self.crs, self.transform, (self.height, self.width))
        else:
            dst_crs = dst_crs if isinstance(dst_crs, CRS) else CRS.from_user_input(dst_crs)

        # Old rasterio compatibility: a separate check for validity
        if not dst_crs.is_valid:
            raise rasterio.errors.CRSError('Invalid CRS {} given'.format(dst_crs))

        # get temporary filepath if such is not provided
        tmp_file = False if fp is not None else True
        if fp is None:
            fp = '{tmp}/reprojected_{crs}/{directory}/{name}.tif'.format(
                tmp=TMP_DIR, crs=dst_crs, directory=random_name(), name=self.name)
        os.makedirs(os.path.dirname(fp), exist_ok=True)

        # calculate params of new reprojected Band
        transform, width, height = calculate_default_transform(
            self.crs, dst_crs, self.width, self.height, *self.bounds)
        kwargs = self.meta.copy()
        kwargs.update({
            'crs': dst_crs,
            'transform': transform,
            'width': width,
            'height': height
        })

        # reproject
        with rasterio.open(fp, 'w', **kwargs) as dst:
                reproject(
                    source=rasterio.band(self._band, 1),
                    destination=rasterio.band(dst, 1),
                    src_transform=self.transform,
                    src_crs=self.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=getattr(Resampling, interpolation))

        # new band
        band = Band(fp)
        band._tmp_file = tmp_file # file will be automatically removed when `Band` instance will be deleted
        return band

    def reproject_to_utm(self, fp=None, interpolation='nearest'):
        """

        Alias of `reproject` method with automatic Band utm zone determining
        The utm zone is determined according to the center of the bounding box of the collection.
        Does not suit to large area images, that would not fit into one zone (about 6 dergees in longitude)

        Args:
            fp: file to save the new reprojected band. If None, a new temporary file is created
            interpolation: type of the raster interpolation. 'nearest', 'bilinear', 'cubic' or other supported by GDAL

        Returns:
            new Band object pointing to the fp
        """
        dst_crs = get_utm_zone(self.crs, self.transform, (self.height, self.width))
        return self.reproject(dst_crs, fp=fp, interpolation=interpolation)


    def generate_samples(self, width, height):
        """
        Yield `Sample`s with defined grid
        Args:
            width: dimension of sample in pixels and step along `X` axis
            height: dimension of sample in pixels and step along `Y` axis

        Returns:
            Generator object
        """
        for x in range(0, self.width, width):
            for y in range(0, self.height, height):
                yield self.sample(y, x, height, width)


class BandSample(GeoObject):
    """
    Sample is a wrapper over the georeferenced image data already read from the disk
    """

    def __init__(self, name, raster, crs, transform, nodata=0):

        super().__init__()

        self._name = name
        self._raster = band_shape_guard(raster)
        self._nodata = nodata
        self._transform = Affine(*transform) if not isinstance(transform, Affine) else transform
        self._crs = CRS.from_user_input(crs) if not isinstance(crs, CRS) else crs

        # Old rasterio compatibility: a separate check for validity
        if not self._crs.is_valid:
            raise rasterio.errors.CRSError('Invalid CRS {} given'.format(crs))

    def __eq__(self, other):
        res = np.allclose(self.numpy(), other.numpy())
        res = res and (self.crs == other.crs)
        res = res and np.allclose(np.array(self.transform), np.array(other.transform))
        return res

    def __repr__(self):
        return '<BandSample: name={}, shape={}, dtype={}>'.format(self.name,
                                                                  self.shape,
                                                                  self.dtype)

    # ======================== PROPERTY BLOCK ========================
    @property
    def width(self):
        return self._raster.shape[1]

    @property
    def height(self):
        return self._raster.shape[0]

    @property
    def count(self):
        return 1

    @property
    def shape(self):
        return self.height, self.width

    @property
    def dtype(self):
        return self._raster.dtype

    @property
    def res(self):
        return abs(self.transform.a), abs(self.transform.e)

    @property
    def transform(self):
        return self._transform

    @property
    def crs(self):
        return self._crs

    @property
    def nodata(self):
        return self._nodata

    @property
    def bounds(self):
        left = self.transform.c
        top = self.transform.f
        right= left + self.transform.a * self.width
        bottom = top + self.transform.e * self.height
        return BoundingBox(left, bottom, right, top)

    @property
    def name(self):
        return self._name

    # ======================== METHODS BLOCK ========================

    @classmethod
    def from_file(cls, fp):
        band = Band(fp)
        return band.sample(0, 0, band.width, band.height)

    def same(self, other):
        """
        Compare if samples have same resolution, crs and shape
        Args:
            other: Band to compare with

        Returns: Bool

        """
        res = True
        res = res and (self.crs == other.crs)
        res = res and (self.transform == self.transform)
        res = res and (self.height == self.height)
        res = res and (self.width == self.width)
        return res

    def save(self, directory, ext='.tif', **kwargs):
        """
        Saves the sample to the tiff file, does not create a Band.
        Args:
            directory: path to save the image
            ext: any string that will be attached to the sample name, by default '.tif'
            **kwargs: any kwargs to be passed to rasterio.open

        Returns:

        """

        fp = os.path.join(directory, self._name + ext)
        with rasterio.open(fp, mode='w', driver='GTiff', width=self.width,
                           height=self.height, count=1, crs=self.crs.get('init'),
                           transform=self.transform, nodata=self.nodata,
                           dtype=self.dtype, **kwargs) as dst:
            dst.write(self._raster.squeeze(), 1)

    def sample(self, y, x, height, width):
        """
        Subsample of Sample with specified size and position
        Args:
            y: pixel coordinates of left top corner
            x: pixel coordinates of left top corner
            height: spatial dimension of sample in pixels
            width: spatial dimension of sample in pixels

        Returns: new `Sample` object
        """

        coord_x = self.transform.c + x * self.transform.a
        coord_y = self.transform.f + y * self.transform.e

        dst_transform = Affine(self.transform.a, self.transform.b, coord_x,
                               self.transform.d, self.transform.e, coord_y)
        dst_raster = self._raster[y:y+height, x:x+width]

        return BandSample(self.name, dst_raster, self.crs, dst_transform, self.nodata)

    def reproject(self, dst_crs, interpolation='nearest'):
        """
            Reprojects the BandSample to specified crs
            Args:
                dst_crs: new crs, either in the rasterio.CRS format, or in interpretable string, \
                or 'utm' for automatic selection of the utm zone
                interpolation: type of the raster interpolation. 'nearest', 'bilinear', 'cubic' or other supported by GDAL

            Returns:
                new BandSample object in the new projection
            """
        # Direct comparison of CRS to string raises error in some versions of rasterio
        if isinstance(dst_crs, str) and dst_crs == 'utm':
            dst_crs = get_utm_zone(self.crs, self.transform, (self.height, self.width))
        else:
            dst_crs = dst_crs if isinstance(dst_crs, CRS) else CRS.from_user_input(dst_crs)

        # Old rasterio compatibility: a separate check for validity
        if not dst_crs.is_valid:
            raise rasterio.errors.CRSError('Invalid CRS {} given'.format(dst_crs))

        dst_transform, dst_width, dst_height = calculate_default_transform(
            self.crs, dst_crs, self.width, self.height, *self.bounds)

        new_raster = np.empty(shape=(1, dst_height, dst_width), dtype=self.dtype)

        reproject(
            self._raster, new_raster,
            src_transform=self.transform,
            dst_transform=dst_transform,
            src_crs=self.crs,
            dst_crs=dst_crs,
            resampling=getattr(Resampling, interpolation))

        return BandSample(self.name, new_raster, dst_crs, dst_transform, self.nodata)

    def reproject_to_utm(self, interpolation='nearest'):
        """
        Alias of `reproject` method with automatic Band utm zone determining
        The utm zone is determined according to the center of the bounding box of the collection.
        Does not suit to large area geometry, that would not fit into one zone (about 6 dergees in longitude)

        Args:
            interpolation: type of the raster interpolation. 'nearest', 'bilinear', 'cubic' or other supported by GDAL
        Returns:
            new BandSample object in the new projection
        """
        dst_crs = get_utm_zone(self.crs, self.transform, (self.height, self.width))
        return self.reproject(dst_crs, interpolation=interpolation)

    def resample(self, dst_res=None, dst_shape=None, interpolation='nearest'):
        """
            Resamples the BandSample to specified resolution or shape
            Args:
                dst_res: new resolution, in projection units
                dst_shape: out shape, preferrable over
                interpolation: type of the raster interpolation. 'nearest', 'bilinear', 'cubic' or other supported by GDAL

            Returns:
                new BandSample object in the new projection
            """
        transform = self.transform if dst_res is None else Affine(dst_res[1],
                                                                  self.transform.b,
                                                                  self.transform.c,
                                                                  self.transform.d,
                                                                  - dst_res[0],
                                                                  self.transform.f)

        if dst_res is not None and dst_shape is None:
            target_height = int(self.height * self.res[0] / dst_res[0])
            target_width = int(self.width * self.res[1] / dst_res[1])
        elif dst_shape is not None:
            target_height = dst_shape[1]
            target_width = dst_shape[2]
        else:
            target_height = self.height
            target_width = self.width

        new_raster = np.empty(shape=(1, target_height, target_width), dtype=self.dtype)

        reproject(
            self._raster, new_raster,
            src_transform=self.transform,
            dst_transform=transform,
            src_crs=self.crs,
            dst_crs=self.crs,
            resampling=getattr(Resampling, interpolation))

        return BandSample(self._name, new_raster, self.crs, transform, self.nodata)

    def numpy(self):
        return self._raster

    def generate_samples(self, width, height):
        """
        Yield `Sample`s with defined grid
        Args:
            width: dimension of sample in pixels and step along `X` axis
            height: dimension of sample in pixels and step along `Y` axis

        Returns:
            Generator object
        """
        for x in range(0, self.width, width):
            for y in range(0, self.height, height):
                yield self.sample(y, x, height, width)
